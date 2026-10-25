"""
Fröccs core - apps, settings, Windows environments, installing and launching.

How it works, from the basics:

* Wine translates Windows API calls into Linux ones. Proton is Valve's Wine
  build with extra parts (DXVK, VKD3D, fixes). We never call Wine directly:
  we call `umu-run`, which starts Proton inside Valve's Steam Runtime
  container - the same environment Steam uses, just without Steam.

* Every app gets its own *prefix*: a folder that looks like a fresh Windows
  install (drive_c + registry files). Apps can't break each other this way.

* Two ready-made *templates* are built once and copied for new apps:
  "base" (fonts, Visual C++, MSXML, GDI+) and "dotnet" (base + .NET 4.8).
  The installer analysis decides which one an app needs. On Fedora's Btrfs
  the copy is a reflink: instant and no extra disk space.
"""
from __future__ import annotations

import fcntl
import glob
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import time
import uuid
import zlib
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Optional

from . import proton, registry
from .i18n import _
from .paths import (APP_ID, DATA_DIR, DB_FILE, DESKTOP_DIR, ICON_DIR, LAUNCHER,  # noqa: F401
                    LOG_DIR, PREFIX_DIR, SETTINGS_FILE, SNAPSHOT_DIR, TEMPLATE_DIR,
                    drive_c, wine_root)

Progress = Callable[[str, float], None]

# ------------------------------------------------------------- templates --

TEMPLATES = {
    "base": ("corefonts", "vcrun2022", "msxml6", "gdiplus", "fontsmooth=rgb"),
    "dotnet": ("dotnet48",),            # built on top of a copy of "base"
}

VERB_NAMES = {
    "corefonts": "Microsoft core fonts",
    "vcrun2022": "Visual C++ runtime",
    "dotnet48": ".NET Framework 4.8 (slow, about 10 minutes)",
    "msxml6": "MSXML 6",
    "gdiplus": "GDI+",
    "mfc42": "MFC runtime",
    "fontsmooth=rgb": "font smoothing",
    "arial": "Arial font",
    "vcrun2005": "Visual C++ 2005 runtime",
    "vcrun2008": "Visual C++ 2008 runtime",
    "vcrun2013": "Visual C++ 2013 runtime",
}

DLL_MODES = {            # what the user picks -> WINEDLLOVERRIDES syntax
    "native,builtin": "n,b",
    "builtin,native": "b,n",
    "native": "n",
    "builtin": "b",
    "disabled": "",
}

# ------------------------------------------------------------- settings --

DEFAULT_SETTINGS = {
    "default_scale": 100,
    "view": "list",             # list | grid
    "nocow": False,             # Btrfs: no copy-on-write for prefixes
    "speed_mode": False,        # skip fsync during installs (libeatmydata)
    "auto_snapshots": True,     # restore point before running other programs
    "prebuild": True,           # build templates in the background
    "language": "auto",         # auto | en | hu
}


def load_settings() -> dict:
    s = dict(DEFAULT_SETTINGS)
    try:
        s.update(json.loads(SETTINGS_FILE.read_text()))
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return s


def save_settings(settings: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(settings, indent=2))
    apply_nocow(settings.get("nocow", False))


def apply_nocow(enabled: bool) -> None:
    """
    Btrfs "copy-on-write" makes the many tiny save-now writes of installers
    slow. chattr +C on a folder makes NEW files inside it skip it (no
    compression and no cheap reflink copies from normal folders).
    """
    if not shutil.which("chattr"):
        return
    for d in (PREFIX_DIR, TEMPLATE_DIR, SNAPSHOT_DIR):
        d.mkdir(parents=True, exist_ok=True)
        subprocess.run(["chattr", "+C" if enabled else "-C", str(d)], capture_output=True)


# ----------------------------------------------------------------- apps --

@dataclass
class App:
    id: str
    name: str
    exe: str = ""                 # main program (Linux path inside the prefix)
    scale: int = 100              # percent
    dotnet: bool = False          # created from the .NET template
    graphics: str = "dxvk"        # dxvk (Vulkan) | wined3d (OpenGL)
    wayland: bool = False
    ntsync: bool = False
    debug: bool = False
    proton: str = ""              # "" = newest GE-Proton, else a pinned build
    args: str = ""                # extra launch arguments
    env: dict = field(default_factory=dict)
    launch_options_on: bool = True
    dll_overrides: dict = field(default_factory=dict)
    shortcuts: list = field(default_factory=list)   # extra menu entries [{name, exe}]
    file_types: list = field(default_factory=list)  # [{ext, shortcut}] shortcut -1 = main
    file_types_removed: list = field(default_factory=list)
    protocols: list = field(default_factory=list)
    autostart: bool = False
    tray_window: bool = False     # Wine's little tray-icon window (GNOME has no tray)
    scale_auto: bool = False      # follow the screen's scaling at each start
    gamemode: bool = False        # Feral GameMode while running
    mangohud: bool = False        # FPS/performance overlay
    fps_limit: int = 0            # 0 = no limit
    fonts: list = field(default_factory=list)       # font files we installed
    share_home: bool = True       # Documents, Desktop... = your real folders
    drives: dict = field(default_factory=dict)      # {"d": "/path"}
    hide_z: bool = False
    com_ports: dict = field(default_factory=dict)   # {"COM1": "/dev/serial/by-id/..."}
    recipe: str = ""
    icon: str = ""
    profile: str = ""             # pre-0.3 field, only read for migration

    @property
    def prefix(self) -> Path:
        return PREFIX_DIR / self.id

    @property
    def desktop_file(self) -> Path:
        return DESKTOP_DIR / f"froccs-{self.id}.desktop"

    @property
    def log_file(self) -> Path:
        return LOG_DIR / f"{self.id}.log"

    def shortcut_exe(self, index: int) -> str:
        if index < 0 or index >= len(self.shortcuts):
            return self.exe
        return self.shortcuts[index]["exe"]


def _migrate(app: App) -> App:
    """Apps made by 0.1/0.2 had a 'profile' instead of separate settings."""
    if app.profile:
        if app.profile in ("autodesk", "plc"):
            app.dotnet = True
        if app.profile == "plc":
            app.graphics = "wined3d"
        app.profile = ""
    return app


def load_apps() -> list[App]:
    try:
        raw = json.loads(DB_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return []
    known = App.__dataclass_fields__
    return [_migrate(App(**{k: v for k, v in item.items() if k in known})) for item in raw]


def save_apps(apps: list[App]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = DB_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps([asdict(a) for a in apps], indent=2))
    tmp.replace(DB_FILE)          # atomic: never a half-written file


def get_app(app_id: str) -> Optional[App]:
    return next((a for a in load_apps() if a.id == app_id), None)


def upsert_app(app: App) -> None:
    apps = [a for a in load_apps() if a.id != app.id]
    apps.append(app)
    apps.sort(key=lambda a: a.name.lower())
    save_apps(apps)


def make_id(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:32] or "app"
    return f"{slug}-{uuid.uuid4().hex[:6]}"


def guess_name(installer: Path) -> str:
    """'AutoCAD_2025_English_win_64bit_dlm.sd.exe' -> 'AutoCAD 2025'."""
    stem = re.sub(r"[_\-.]+", " ", installer.stem)
    year = r"(?:19|20)\d\d"
    junk = (r"\b(setup|install(er)?|x64|x86|win(64|32)?|64 ?bit|32 ?bit|en[ -]?us|"
            r"english|dlm|sd|full|offline|web|v?(?!" + year + r"\b)\d+(\s\d+)*)\b")
    name = re.sub(junk, " ", stem, flags=re.I)
    name = re.sub(r"\s+", " ", name).strip()
    return name or installer.stem


# ---------------------------------------------------------- dependencies --

def umu_path() -> Optional[str]:
    """umu-run from PATH, or the copy install.sh puts in ~/.local/bin."""
    local = Path.home() / ".local" / "bin" / "umu-run"
    return shutil.which("umu-run") or (str(local) if os.access(local, os.X_OK) else None)


def missing_dependencies() -> tuple[list[str], list[str]]:
    required = [] if umu_path() else ["umu-launcher"]
    optional = [] if (shutil.which("wrestool") and shutil.which("icotool")) else ["icoutils"]
    return required, optional


EATMYDATA = next(iter(sorted(
    p for d in ("/usr/lib64", "/usr/lib", "/usr/lib/x86_64-linux-gnu")
    for p in glob.glob(f"{d}/libeatmydata.so*"))), None)


def ntsync_available() -> bool:
    return os.path.exists("/dev/ntsync") and os.access("/dev/ntsync", os.R_OK | os.W_OK)


# -------------------------------------------------------- environment --

def dll_override_string(overrides: dict) -> str:
    parts = ["winemenubuilder.exe=d"]      # no duplicate menu entries from Wine
    for dll, mode in overrides.items():
        dll = dll.strip()
        if dll:
            parts.append(f"{dll}={DLL_MODES.get(mode, mode)}")
    return ";".join(parts)


def base_env(prefix: Path, pinned: str = "", verb: Optional[str] = None) -> dict:
    env = os.environ.copy()
    # Fröccs may have switched its own window to another language; programs
    # keep the system's language (i18n.apply_toolkit_language).
    if "FROCCS_SYSTEM_LANGUAGE" in env:
        original = env.pop("FROCCS_SYSTEM_LANGUAGE")
        if original:
            env["LANGUAGE"] = original
        else:
            env.pop("LANGUAGE", None)
    env.update({
        "WINEPREFIX": str(prefix),
        "GAMEID": "0",                 # no game-specific protonfixes
        "STORE": "none",
        "PROTONPATH": proton.resolve(pinned),
        "WINEDEBUG": "-all",           # Wine debug output costs CPU
        "WINEDLLOVERRIDES": dll_override_string({}),
    })
    if verb:
        # Proton's default 'waitforexitandrun' first waits for everything
        # already running in the prefix to exit. 'run' joins it right away -
        # needed e.g. so a login link reaches the running program.
        env["PROTON_VERB"] = verb
    return env


# Proton names every window "steam_app_<game id>" (its X11 WM_CLASS). With the
# same id for everything, GNOME can't tell programs apart and the dash shows a
# generic icon called "steam_app_0". So each program, and each extra menu entry,
# gets its own stable number. umu takes the trailing digits of GAMEID as the
# Steam app id. The numbers start at 1.9 billion, far above any real Steam game,
# so no game-specific Proton/umu fixes are applied by accident.
GAME_ID_BASE = 1_900_000_000


def game_id(app: App, shortcut: int = -1) -> int:
    return GAME_ID_BASE + (zlib.crc32(app.id.encode()) % 20_000_000) * 10 + (shortcut + 1) % 10


def window_class(app: App, shortcut: int = -1) -> str:
    return f"steam_app_{game_id(app, shortcut)}"


def app_env(app: App, verb: Optional[str] = None, launch: bool = False,
            shortcut: int = -1) -> dict:
    env = base_env(app.prefix, app.proton, verb)
    env["GAMEID"] = f"umu-{game_id(app, shortcut)}"
    env["WINEDLLOVERRIDES"] = dll_override_string(app.dll_overrides)
    if app.graphics == "wined3d":
        env["PROTON_USE_WINED3D"] = "1"
    if app.wayland:
        env["PROTON_ENABLE_WAYLAND"] = "1"
    if app.ntsync and ntsync_available():
        env["PROTON_USE_NTSYNC"] = "1"
    if app.debug:
        env.pop("WINEDEBUG", None)
        env["PROTON_LOG"] = "1"
        env["PROTON_LOG_DIR"] = str(LOG_DIR / "debug")
        env["UMU_LOG"] = "debug"
        (LOG_DIR / "debug").mkdir(parents=True, exist_ok=True)
    if app.fps_limit > 0:
        env["DXVK_FRAME_RATE"] = str(app.fps_limit)       # DirectX 9-11
        env["VKD3D_FRAME_RATE"] = str(app.fps_limit)      # DirectX 12
    if launch and app.launch_options_on:
        env.update({k: str(v) for k, v in app.env.items()})
    return env


# ---------------------------------------------------- running processes --

def _run(args: list[str], env: dict, log: Path, cwd: Optional[Path] = None,
         stop=None) -> int:
    """Run `umu-run <args>` and wait (or until `stop` is set). Output -> log."""
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "a") as lf:
        lf.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')}  umu-run {' '.join(args)}\n")
        lf.flush()
        proc = subprocess.Popen([umu_path() or "umu-run", *args], env=env, cwd=cwd,
                                stdin=subprocess.DEVNULL, stdout=lf,
                                stderr=subprocess.STDOUT)
        while True:
            try:
                return proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                if stop is not None and stop.is_set():
                    return 0          # leave it running; the user said it's done


def prefix_pids(prefix: Path) -> list[int]:
    """
    PIDs of every process running inside this prefix. Installers often start
    a child and exit at once, so we look at all processes: each Wine process
    carries WINEPREFIX in its environment.
    """
    want = str(prefix).encode()
    me, pids = os.getpid(), []
    for entry in os.listdir("/proc"):
        if not entry.isdigit() or int(entry) == me:
            continue
        try:
            environ = Path(f"/proc/{entry}/environ").read_bytes()
        except OSError:
            continue
        for var in environ.split(b"\0"):
            if var.startswith(b"WINEPREFIX="):
                val = var[11:].rstrip(b"/")
                if val == want or val.startswith(want + b"/"):
                    pids.append(int(entry))
                break
    return pids


def is_running(app: App) -> bool:
    return bool(prefix_pids(app.prefix))


def wait_until_idle(prefix: Path, stop=None, poll: float = 2.0) -> None:
    """Wait until nothing runs in the prefix (or `stop`, a threading.Event)."""
    while prefix_pids(prefix):
        if stop is not None:
            if stop.wait(poll):
                return
        else:
            time.sleep(poll)


def kill_prefix(prefix: Path) -> int:
    pids = prefix_pids(prefix)
    for pid in pids:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    return len(pids)


def copy_tree(src: Path, dst: Path) -> None:
    """Reflink copy: instant and free on Btrfs/XFS, normal copy elsewhere."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["cp", "-a", "--reflink=auto", str(src), str(dst)], check=True)


# --------------------------------------------------------------- scaling --

def apply_scale(app: App) -> None:
    """
    Windows' scaling = LogPixels: 96 = 100 %, 120 = 125 %, 144 = 150 %.

    Wine has stored it in two places over the years:
      HKCU\\Control Panel\\Desktop\\LogPixels   (per user, older Wine)
      HKCC\\Software\\Fonts\\LogPixels          (what winecfg writes, and what
                                              current Wine/Proton reads)
    HKCC (HKEY_CURRENT_CONFIG) isn't a real key but a link to
    HKLM\\System\\CurrentControlSet\\Hardware Profiles\\Current, which is itself a
    link. Editing system.reg by hand could create a broken duplicate there, so
    we let Wine's own regedit write both keys.
    """
    dpi = round(96 * app.scale / 100)
    root = wine_root(app.prefix)
    user_reg = root / "user.reg"
    if user_reg.exists() and not is_running(app):
        registry.set_dword(user_reg, "Control Panel\\Desktop", "LogPixels", dpi)
    tmp = drive_c(app.prefix) / "windows" / "temp"
    tmp.mkdir(parents=True, exist_ok=True)
    reg_file = tmp / "froccs-dpi.reg"
    reg_file.write_text("REGEDIT4\r\n\r\n"
                        "[HKEY_CURRENT_USER\\Control Panel\\Desktop]\r\n"
                        f'"LogPixels"=dword:{dpi:08x}\r\n\r\n'
                        "[HKEY_CURRENT_CONFIG\\Software\\Fonts]\r\n"
                        f'"LogPixels"=dword:{dpi:08x}\r\n', encoding="ascii")
    _run(["regedit", "/S", r"C:\windows\temp\froccs-dpi.reg"],
         app_env(app, "run"), app.log_file)
    reg_file.unlink(missing_ok=True)


def read_scale(app: App) -> dict[str, Optional[int]]:
    """What the prefix's registry files say right now, for diagnostics."""
    root = wine_root(app.prefix)
    out: dict[str, Optional[int]] = {}
    for label, fname, pattern in (
            ("HKCU Control Panel\\Desktop", "user.reg", r"control panel\\desktop"),
            ("HKCC Software\\Fonts", "system.reg",
             r"system\\(currentcontrolset|controlset\d+)\\hardware profiles\\(current|\d+)"
             r"\\software\\fonts")):
        out[label] = None
        for key, values in registry.read_sections(root / fname).items():
            if re.fullmatch(pattern, key) and "logpixels" in values:
                m = re.match(r"dword:([0-9a-f]+)", values["logpixels"])
                if m:
                    out[label] = round(int(m.group(1), 16) * 100 / 96)
    return out


# -------------------------------------------------------------- templates --

class InstallError(RuntimeError):
    pass


def running_installer_text() -> str:
    return _("Running the installer\nFollow the installer's own window. If it "
             "starts the program at the end, close the program to continue.")


@contextmanager
def _template_lock(key: str, progress: Optional[Progress] = None):
    """Only one process builds a template at a time (window + installer + CLI)."""
    TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)
    with open(TEMPLATE_DIR / f".{key}.lock", "w") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            if progress:
                progress(_("Waiting for the background preparation to finish…"), 0.05)
            fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def template_ready(key: str) -> bool:
    return (TEMPLATE_DIR / key / ".froccs-ready").exists()


def ensure_template(key: str, progress: Progress) -> list[str]:
    """Build a template once. Returns the components that failed."""
    if key == "dotnet":
        ensure_template("base", lambda t, f: progress(t, f * 0.3))
    with _template_lock(key, progress):
        tpl = TEMPLATE_DIR / key
        marker = tpl / ".froccs-ready"
        if marker.exists():
            return json.loads(marker.read_text()).get("failed", [])
        shutil.rmtree(tpl, ignore_errors=True)
        env = base_env(tpl)
        log = LOG_DIR / f"template-{key}.log"
        start = 0.3 if key == "dotnet" else 0.0

        if key == "dotnet":
            progress(_("Preparing the .NET environment"), start)
            copy_tree(TEMPLATE_DIR / "base", tpl)
            (tpl / ".froccs-ready").unlink(missing_ok=True)
        else:
            tpl.mkdir(parents=True)
            progress(_("Creating the Windows environment\n(the first time this downloads "
                       "Proton, a few minutes)"), 0.0)
            _run([""], env, log)
            wait_until_idle(tpl)
            if not drive_c(tpl).is_dir():
                shutil.rmtree(tpl, ignore_errors=True)
                raise InstallError(_("Could not create the Windows environment.\nSee {log}").format(log=log))

        verbs = TEMPLATES[key]
        failed = []
        for n, verb in enumerate(verbs):
            progress(_("Installing {what}").format(what=_(VERB_NAMES.get(verb, verb))),
                     start + (1 - start) * (n + 1) / (len(verbs) + 1))
            if _run(["winetricks", "-q", verb], env, log) != 0:
                failed.append(verb)
            wait_until_idle(tpl)
        marker.write_text(json.dumps({"failed": failed, "built": time.time(),
                                      "proton": proton.effective_name()}))
        return failed


def prebuild_templates(progress: Progress) -> None:
    """
    Background job: have both templates ready before they're needed.
    `progress` gets the current step and the OVERALL fraction (0..1). The .NET
    part takes ~3/4 of the total time, so it gets 3/4 of the bar.
    """
    if not umu_path():
        return
    need_base = not template_ready("base")
    if need_base:
        ensure_template("base", lambda t, f: progress(t, f * 0.25))
    if not template_ready("dotnet"):
        start = 0.25 if need_base else 0.0
        ensure_template("dotnet", lambda t, f: progress(t, start + f * (1 - start)))
    progress(_("Done"), 1.0)


# ------------------------------------------------------- finding programs --

_SKIP = re.compile(r"(unins|uninst|setup|install|update|crash|report|helper|redist|"
                   r"dotnet|ndp\d|repair|elevat|service|svc|webview|cefsharp|"
                   r"vc_?redist|activation|licen[cs]e|feedback|sso|adsk|senddmp|"
                   r"cer_|webbrowser|qtwebengine|register|diag)", re.I)


def list_exes(prefix: Path) -> dict[str, int]:
    root, found = drive_c(prefix), {}
    if not root.is_dir():
        return found
    for dirpath, dirnames, files in os.walk(root):
        if Path(dirpath) == root:      # Wine's own C:\windows is never the answer
            dirnames[:] = [d for d in dirnames if d.lower() != "windows"]
        for f in files:
            if f.lower().endswith(".exe"):
                p = os.path.join(dirpath, f)
                try:
                    found[p] = os.path.getsize(p)
                except OSError:
                    pass
    return found


def rank_candidates(before: dict, after: dict, limit: int = 12) -> list[Path]:
    new = [p for p in after if p not in before]
    new.sort(key=lambda p: (bool(_SKIP.search(Path(p).name)), -after[p]))
    return [Path(p) for p in new[:limit]]


# ---------------------------------------------------------------- install --

def _reg_add_args(key: str, name, rtype, data) -> list[str]:
    args = ["reg", "add", key]
    if name:
        args += ["/v", name]
    if rtype:
        args += ["/t", rtype]
    if data is not None:
        args += ["/d", str(data)]
    return args + ["/f"]


def install(app: App, installer: Path, progress: Progress, stop=None,
            extra_verbs: tuple = (), reg_edits: tuple = ()) -> tuple[list[Path], list[str]]:
    """
    Full install flow (runs for minutes - call from a thread). Returns
    (candidate programs, failed components). `stop` set = user says the
    installer is done.
    """
    from . import devices                      # avoids an import cycle

    key = "dotnet" if app.dotnet else "base"
    failed = ensure_template(key, lambda t, f: progress(t, f * 0.55))

    progress(_("Preparing a private copy for this app"), 0.57)
    if app.prefix.exists():
        raise InstallError(_("{path} already exists").format(path=app.prefix))
    copy_tree(TEMPLATE_DIR / key, app.prefix)
    (app.prefix / ".froccs-ready").unlink(missing_ok=True)

    progress(_("Setting display scaling to {pct} %").format(pct=app.scale), 0.6)
    apply_scale(app)
    devices.apply_all(app)

    env = app_env(app)
    if load_settings().get("speed_mode") and EATMYDATA:
        # Turn "save to disk NOW" calls into no-ops during the install only.
        env["LD_PRELOAD"] = (EATMYDATA + " " + env.get("LD_PRELOAD", "")).strip()

    for n, verb in enumerate(extra_verbs):
        progress(_("Installing {what}").format(what=_(VERB_NAMES.get(verb, verb))), 0.6 + 0.08 * n / len(extra_verbs))
        if _run(["winetricks", "-q", verb], env, app.log_file) != 0:
            failed.append(verb)
        wait_until_idle(app.prefix)
    if reg_edits:
        progress(_("Applying program-specific settings"), 0.69)
        for edit in reg_edits:
            _run(_reg_add_args(*edit), env, app.log_file)
        wait_until_idle(app.prefix)

    before = list_exes(app.prefix)
    progress(running_installer_text(), 0.7)
    if installer.suffix.lower() == ".msi":
        args = ["msiexec", "/i", devices.to_windows_path(app, installer)]
    else:
        args = [str(installer)]
    _run(args, env, app.log_file, cwd=installer.parent, stop=stop)
    wait_until_idle(app.prefix, stop)
    devices.clean_documents_clutter()          # Wine's empty Music, Pictures... folders

    progress(_("Looking for the installed program"), 0.97)
    return rank_candidates(before, list_exes(app.prefix)), failed


def discard(app: App) -> None:
    """Throw away a failed or cancelled install."""
    kill_prefix(app.prefix)
    shutil.rmtree(app.prefix, ignore_errors=True)


# ---------------------------------------------------------------- launch --

def _trim_log(log: Path, max_bytes: int = 5_000_000) -> None:
    if log.exists() and log.stat().st_size > max_bytes:
        log.write_bytes(log.read_bytes()[-max_bytes // 2:])


def _user_args(app: App) -> list[str]:
    if not (app.launch_options_on and app.args.strip()):
        return []
    try:
        return shlex.split(app.args)
    except ValueError:
        return app.args.split()


def launch_detached(app: App, exe: Optional[str] = None, args: Optional[list] = None,
                    user_args: bool = False) -> None:
    """Start the app (or another exe in its prefix) without waiting."""
    from . import devices, screens
    devices.apply_on_launch(app)
    screens.apply_auto(app)
    target = exe or app.exe
    _trim_log(app.log_file)
    app.log_file.parent.mkdir(parents=True, exist_ok=True)
    extra = _user_args(app) if user_args else []
    cmd = [*wrappers(app), umu_path() or "umu-run", target, *extra,
           *devices.win_args(app, args or [])]
    cwd = Path(target).parent if os.path.isabs(target) and Path(target).parent.is_dir() else None
    with open(app.log_file, "a") as log:
        subprocess.Popen(cmd, env=app_env(app, "run", launch=True), cwd=cwd,
                         stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                         start_new_session=True)


def exec_in_prefix(app: App, exe: str, args: list[str], shortcut: int = -1) -> None:
    """Become the umu-run process (no extra Python process left behind)."""
    from . import devices, screens
    devices.apply_on_launch(app)
    screens.apply_auto(app)
    _trim_log(app.log_file)
    app.log_file.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(app.log_file, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    os.dup2(fd, 1)
    os.dup2(fd, 2)
    if Path(exe).parent.is_dir():
        os.chdir(Path(exe).parent)
    cmd = [*wrappers(app), umu_path() or "umu-run", exe, *args]
    os.execvpe(cmd[0], cmd, app_env(app, "run", launch=True, shortcut=shortcut))


def wrappers(app: App) -> list[str]:
    """Programs that start the app for us: gamemoderun / mangohud."""
    out = []
    if app.gamemode and shutil.which("gamemoderun"):
        out.append("gamemoderun")
    if app.mangohud and shutil.which("mangohud"):
        out.append("mangohud")
    return out


def launcher_command() -> str:
    if LAUNCHER.exists():
        return f'"{LAUNCHER}"'
    pkg_parent = Path(__file__).resolve().parent.parent      # source checkout
    import sys
    return f'env PYTHONPATH="{pkg_parent}" "{sys.executable}" -m froccs'


def schedule_rescan(app: App) -> None:
    """Programs may register login links / file types on first start or after
    an update, so re-check a little after each launch."""
    cmd = launcher_command()
    subprocess.Popen(["sh", "-c", f"sleep 20; {cmd} links {app.id} >/dev/null; "
                                  f"sleep 70; {cmd} links {app.id} >/dev/null"],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)


def launch_shortcut(app: App, index: int, files: list[str]) -> None:
    """Used by the apps-menu entries."""
    from . import devices
    schedule_rescan(app)
    exec_in_prefix(app, app.shortcut_exe(index),
                   _user_args(app) + devices.win_args(app, files), shortcut=index)
