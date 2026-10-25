"""
What a Windows program can see: folders, drive letters, serial ports.

Wine's drive letters are just symlinks in <prefix>/dosdevices:
    c:  -> ../drive_c      (the fake Windows drive)
    z:  -> /               (your whole Linux system)
    d:  -> /home/you/Work  (anything you map)
    com1 -> /dev/ttyUSB0   (serial ports work the same way)
"""
from __future__ import annotations

import glob
import os
import re
import shutil
from pathlib import Path

from . import registry
from .paths import HOME, dosdevices, wine_root, windows_user_dir

# Windows folder name -> XDG user-dir key
SHARED_FOLDERS = {
    "Documents": "DOCUMENTS",
    "Desktop": "DESKTOP",
    "Downloads": "DOWNLOAD",
    "Music": "MUSIC",
    "Pictures": "PICTURES",
    "Videos": "VIDEOS",
}


def xdg_dir(key: str) -> Path | None:
    """Your real folder for e.g. DOCUMENTS (~/Dokumentumok on a Hungarian system)."""
    cfg = Path(os.environ.get("XDG_CONFIG_HOME") or HOME / ".config") / "user-dirs.dirs"
    try:
        for line in cfg.read_text().splitlines():
            m = re.match(rf'^XDG_{key}_DIR="(.*)"$', line.strip())
            if m:
                p = Path(m.group(1).replace("$HOME", str(HOME)))
                return p if p != HOME else None
    except OSError:
        pass
    fallback = {"DOCUMENTS": "Documents", "DESKTOP": "Desktop", "DOWNLOAD": "Downloads",
                "MUSIC": "Music", "PICTURES": "Pictures", "VIDEOS": "Videos"}[key]
    p = HOME / fallback
    return p if p.is_dir() else None


# ------------------------------------------------------------ home folders --

def apply_home_links(app) -> None:
    """share_home on: the program's Documents etc. ARE your folders, so files
    you save from it show up where you expect. Off: private folders."""
    user = windows_user_dir(app.prefix)
    if user is None:
        return
    for win_name, key in SHARED_FOLDERS.items():
        link = user / win_name
        target = xdg_dir(key)
        if app.share_home and target and target.is_dir():
            if link.is_symlink():
                if Path(os.readlink(link)) != target:
                    link.unlink()
                    link.symlink_to(target)
                continue
            if link.is_dir():
                _merge_into(link, target)
            link.symlink_to(target)
        elif not app.share_home and link.is_symlink():
            link.unlink()
            link.mkdir()


def _merge_into(folder: Path, target: Path) -> None:
    """Move what's in the private folder to your real one, never overwriting."""
    leftovers = False
    for item in folder.iterdir():
        dest = target / item.name
        if dest.exists() or dest.is_symlink():
            leftovers = True
            continue
        shutil.move(str(item), dest)
    if leftovers:
        folder.rename(folder.with_name(folder.name + " (Fröccs old)"))
    else:
        folder.rmdir()


# ------------------------------------------------------------------ drives --

def drive_map(app) -> dict[str, Path]:
    """Current letter -> target for every drive in the prefix."""
    out = {}
    dd = dosdevices(app.prefix)
    if dd.is_dir():
        for link in dd.iterdir():
            if re.fullmatch(r"[a-z]:", link.name) and link.is_symlink():
                try:
                    out[link.name[0]] = (dd / os.readlink(link)).resolve()
                except OSError:
                    pass
    return out


def free_letter(app) -> str:
    used = set(drive_map(app)) | set(app.drives)
    return next(l for l in "defghijklmnopqrstuvwxy" if l not in used)


def apply_drives(app) -> None:
    dd = dosdevices(app.prefix)
    if not dd.is_dir():
        return
    managed = set(app.drives)
    for letter in "defghijklmnopqrstuvwxy":
        link = dd / f"{letter}:"
        want = app.drives.get(letter)
        if want:
            if link.is_symlink() and os.readlink(link) == want:
                continue
            if link.is_symlink():
                link.unlink()
            if not link.exists():
                link.symlink_to(want)
        elif letter == "h" and app.hide_z and "h" not in managed:
            # With Z: hidden, files are passed to programs through H: (= home)
            if not link.exists():
                link.symlink_to(str(HOME))
    z = dd / "z:"
    if app.hide_z and z.is_symlink():
        z.unlink()
    elif not app.hide_z and not z.exists():
        z.symlink_to("/")
    if not app.hide_z and "h" not in managed:
        h = dd / "h:"
        if h.is_symlink() and os.readlink(h) == str(HOME):
            h.unlink()


def to_windows_path(app, path) -> str:
    """Linux path -> path the program understands, via the best drive letter."""
    p = Path(path).resolve()
    best, best_len = None, -1
    for letter, target in drive_map(app).items():
        if letter == "c":
            continue
        t = str(target)
        if (str(p) == t or str(p).startswith(t.rstrip("/") + "/")) and len(t) > best_len:
            best, best_len = letter, len(t)
    if best is None:
        return str(p)                       # nothing maps it; better than nothing
    target = str(drive_map(app)[best]).rstrip("/")
    rest = str(p)[len(target):].lstrip("/")
    return f"{best.upper()}:\\" + rest.replace("/", "\\")


def win_args(app, args: list[str]) -> list[str]:
    """Files given by the file manager are Linux paths -> convert them."""
    return [to_windows_path(app, a) if os.path.exists(a) else a for a in args]


# ------------------------------------------------------------ serial ports --

def serial_devices() -> list[tuple[str, str]]:
    """[(stable path, description)]. /dev/serial/by-id names survive replugging,
    unlike ttyUSB0/1 which can swap."""
    out, seen = [], set()
    for link in sorted(glob.glob("/dev/serial/by-id/*")):
        real = os.path.realpath(link)
        seen.add(real)
        nice = re.sub(r"^usb-|-if\d+.*$|-port\d+$", "", os.path.basename(link)).replace("_", " ")
        out.append((link, f"{nice} ({os.path.basename(real)})"))
    for dev in sorted(glob.glob("/dev/ttyUSB*") + glob.glob("/dev/ttyACM*")):
        if dev not in seen:
            out.append((dev, os.path.basename(dev)))
    return out


def serial_permission_ok() -> bool:
    devs = [os.path.realpath(d) for d, _ in serial_devices()]
    return all(os.access(d, os.R_OK | os.W_OK) for d in devs)


def apply_ports(app) -> None:
    """Map COM1-COM8 to Linux devices (registry + dosdevices symlinks)."""
    root = wine_root(app.prefix)
    sysreg = root / "system.reg"
    dd = dosdevices(app.prefix)
    for n in range(1, 9):
        name = f"COM{n}"
        dev = app.com_ports.get(name)
        if sysreg.exists():
            registry.set_string(sysreg, "Software\\Wine\\Ports", name, dev or None)
        link = dd / name.lower()
        if dev:
            if link.is_symlink() or link.exists():
                link.unlink()
            link.symlink_to(dev)
        elif link.is_symlink() and not os.readlink(link).startswith("/dev/ttyS"):
            link.unlink()        # was ours (Wine's own defaults point at ttyS*)


# -------------------------------------------------------------- tray icons --

def apply_tray(app) -> None:
    """
    Windows programs put icons in the tray next to the clock. GNOME has no
    tray, so Wine shows them in a small window of its own instead. Wine's
    explorer reads HKCU\\Software\\Wine\\Explorer\\ShowSystray (default on).
    """
    user_reg = wine_root(app.prefix) / "user.reg"
    if not user_reg.exists():
        return
    want = "dword:00000001" if app.tray_window else "dword:00000000"
    if registry.get_values(user_reg, "Software\\Wine\\Explorer").get("showsystray") != want:
        registry.set_dword(user_reg, "Software\\Wine\\Explorer", "ShowSystray",
                           1 if app.tray_window else 0)


# ------------------------------------------------------------------ apply --

def apply_all(app) -> None:
    """Everything here is file edits, so only do it while the app is closed."""
    apply_tray(app)
    apply_home_links(app)
    apply_drives(app)
    apply_ports(app)


def apply_on_launch(app) -> None:
    """Cheap re-check before each start (Proton may re-create Z: on updates)."""
    from .core import is_running
    if is_running(app):
        return
    try:
        apply_drives(app)
        apply_tray(app)          # also fixes apps installed before this setting existed
    except OSError:
        pass
