"""
Making Windows programs feel like Linux apps: menu entries, icons, file
types ("Open with"), login links, start at login.

Linux learns about apps from .desktop files in ~/.local/share/applications.
A .desktop file says how to start the app (Exec=), its icon, and which file
types (MimeType=) and link types (x-scheme-handler/...) it can open.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Optional
from xml.sax.saxutils import escape

from . import core, registry
from .i18n import _
from .paths import (APP_ID, AUTOSTART_DIR, DESKTOP_DIR, ICON_DIR, MIME_DIR,
                    drive_c, wine_root)


# ------------------------------------------------------------------ icons --

def extract_icon(exe: Path, dest_name: str) -> str:
    """Pull the biggest icon out of an .exe (needs icoutils)."""
    if not (shutil.which("wrestool") and shutil.which("icotool")):
        return ""
    ICON_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        subprocess.run(["wrestool", "-x", "-t", "14", str(exe), "-o", str(tmp)],
                       capture_output=True)
        icos = sorted(tmp.glob("*.ico"), key=lambda p: p.stat().st_size, reverse=True)
        if not icos:
            return ""
        out = tmp / "png"
        out.mkdir()
        subprocess.run(["icotool", "-x", "-o", str(out), str(icos[0])], capture_output=True)
        best, best_key = None, (-1, -1)
        for png in out.glob("*.png"):
            m = re.search(r"_(\d+)x(\d+)x(\d+)\.png$", png.name)
            key = (int(m.group(1)), int(m.group(3))) if m else (0, 0)
            if key > best_key:
                best, best_key = png, key
        if not best:
            return ""
        dest = ICON_DIR / f"{dest_name}.png"
        shutil.copy(best, dest)
        return str(dest)


# ------------------------------------------------------------- mime types --

def _system_mime(ext: str) -> Optional[str]:
    """The type Linux already knows for this extension, if any."""
    try:
        import gi
        gi.require_version("Gio", "2.0")
        from gi.repository import Gio
        ctype, uncertain = Gio.content_type_guess(f"file.{ext}", None)
        mime = Gio.content_type_get_mime_type(ctype) if ctype else None
        if mime and not uncertain and mime not in ("application/octet-stream",
                                                   "text/plain"):
            return mime
    except Exception:
        pass
    return None


def mime_for(ext: str) -> str:
    return _system_mime(ext) or f"application/x-froccs-{re.sub(r'[^a-z0-9]+', '-', ext.lower())}"


def _write_mime_package(app: core.App) -> None:
    """Teach Linux our own types for extensions it doesn't know (e.g. .f3d)."""
    pkg = MIME_DIR / "packages" / f"froccs-{app.id}.xml"
    custom = [ft for ft in app.file_types if mime_for(ft["ext"]).startswith("application/x-froccs-")]
    if not custom:
        if pkg.exists():
            pkg.unlink()
            _update_mime_db()
        return
    pkg.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(
        f'  <mime-type type="{mime_for(ft["ext"])}">\n'
        f'    <comment>{escape(app.name)} file (.{escape(ft["ext"])})</comment>\n'
        f'    <glob pattern="*.{escape(ft["ext"])}"/>\n'
        f'  </mime-type>' for ft in custom)
    pkg.write_text('<?xml version="1.0" encoding="UTF-8"?>\n'
                   '<mime-info xmlns="http://www.freedesktop.org/standards/shared-mime-info">\n'
                   f'{body}\n</mime-info>\n')
    _update_mime_db()


def _update_mime_db() -> None:
    if shutil.which("update-mime-database"):
        subprocess.run(["update-mime-database", str(MIME_DIR)], capture_output=True)


def detect_file_types(app: core.App) -> list[dict]:
    """
    File types the program registered for itself in the Windows registry:
    HKCR\\.dwg -> (default) = ProgID ; HKCR\\<ProgID>\\shell\\open\\command.
    Only kept if the command starts one of this app's own shortcuts.
    """
    targets = {os.path.normcase(app.exe): -1}
    for i, s in enumerate(app.shortcuts):
        targets[os.path.normcase(s["exe"])] = i
    found: dict[str, int] = {}
    root = wine_root(app.prefix)
    for reg in ("system.reg", "user.reg"):
        secs = registry.read_sections(root / reg)
        for key, values in secs.items():
            m = re.fullmatch(r"software\\classes\\\.([a-z0-9_\-]{1,16})", key)
            if not m or "@" not in values:
                continue
            progid = values["@"].lower()
            cmd = secs.get(f"software\\classes\\{progid}\\shell\\open\\command", {}).get("@")
            if not cmd:
                continue
            exe = str(win_to_linux(app.prefix, split_command(cmd, "")[0]))
            idx = targets.get(os.path.normcase(exe))
            if idx is not None:
                found[m.group(1)] = idx
    return [{"ext": e, "shortcut": i} for e, i in sorted(found.items())]


def merge_detected_file_types(app: core.App) -> bool:
    have = {ft["ext"] for ft in app.file_types}
    added = False
    for ft in detect_file_types(app):
        if ft["ext"] not in have and ft["ext"] not in app.file_types_removed:
            app.file_types.append(ft)
            added = True
    return added


# ---------------------------------------------------------- desktop files --

def _shortcut_desktop(app: core.App, index: int) -> Path:
    return app.desktop_file if index < 0 else DESKTOP_DIR / f"froccs-{app.id}-s{index}.desktop"


def _desktop_text(app: core.App, index: int) -> str:
    if index < 0:
        name, exe, icon = app.name, app.exe, app.icon
    else:
        s = app.shortcuts[index]
        name, exe, icon = s["name"], s["exe"], s.get("icon") or app.icon
    mimes = sorted({mime_for(ft["ext"]) for ft in app.file_types if ft.get("shortcut", -1) == index})
    lines = [
        "[Desktop Entry]",
        "Type=Application",
        f"Name={' '.join(name.split())}",
        f"Comment=Windows software via Fröccs",
        f"Exec={core.launcher_command()} run {app.id}"
        + (f" --shortcut {index}" if index >= 0 else "") + " %F",
        f"Icon={icon or 'application-x-ms-dos-executable'}",
        "Categories=Utility;",
        # Proton's window class for this entry, so the dash shows the right
        # icon and name (see core.game_id):
        f"StartupWMClass={core.window_class(app, index)}",
        "StartupNotify=true",
        f"X-Froccs-Id={app.id}",
    ]
    if mimes:
        lines.append("MimeType=" + ";".join(mimes) + ";")
    return "\n".join(lines) + "\n"


def write_desktop_files(app: core.App) -> None:
    DESKTOP_DIR.mkdir(parents=True, exist_ok=True)
    _write_mime_package(app)
    wanted = {_shortcut_desktop(app, -1)}
    for i in range(-1, len(app.shortcuts)):
        f = _shortcut_desktop(app, i)
        f.write_text(_desktop_text(app, i))
        f.chmod(0o755)
        wanted.add(f)
    for pattern in (f"froccs-{app.id}-s*.desktop", f"froccs-{app.id}-w*.desktop"):
        for old in DESKTOP_DIR.glob(pattern):
            if old not in wanted:
                old.unlink()
    # Make our app the default only for types nothing else on Linux handles
    if shutil.which("xdg-mime"):
        for ft in app.file_types:
            mime = mime_for(ft["ext"])
            if mime.startswith("application/x-froccs-"):
                subprocess.run(["xdg-mime", "default",
                                _shortcut_desktop(app, ft.get("shortcut", -1)).name, mime],
                               capture_output=True)
    write_autostart(app)
    refresh_menu()


def refresh_menu() -> None:
    if shutil.which("update-desktop-database"):
        subprocess.run(["update-desktop-database", str(DESKTOP_DIR)], capture_output=True)


def write_autostart(app: core.App) -> None:
    f = AUTOSTART_DIR / f"froccs-{app.id}.desktop"
    if app.autostart:
        AUTOSTART_DIR.mkdir(parents=True, exist_ok=True)
        f.write_text(_desktop_text(app, -1).replace(" %F", "")
                     + "X-GNOME-Autostart-enabled=true\n")
    else:
        f.unlink(missing_ok=True)


# --------------------------------------------------------- login links --
#
# Browser logins (Fusion and others) end by opening a link such as
# adskidmgr:/login?code=...  The Windows installer registered "adskidmgr"
# in the registry; we mirror that into Linux as x-scheme-handler/adskidmgr.

_PROTOCOL_BLOCK = {      # never take these over from the real browser / mail app
    "http", "https", "ftp", "ftps", "file", "mailto", "news", "nntp", "telnet",
    "ldap", "callto", "tel", "sms", "irc", "data", "javascript", "about",
    "res", "mk", "its", "ms-its", "steam", "magnet",
}

KNOWN_HANDLERS = {       # used only when the registry has nothing
    "adskidmgr": "AdskIdentityManager.exe",     # Autodesk login (Fusion etc.)
}


def _find_newest(root: Path, name: str) -> Optional[Path]:
    """Newest file called `name` (program updates leave old copies behind)."""
    best, best_t = None, -1.0
    for top in ("Program Files", "Program Files (x86)", "users"):
        base = root / top
        if not base.is_dir():
            continue
        for dirpath, _dirs, files in os.walk(base):
            for f in files:
                if f.lower() == name.lower():
                    p = Path(dirpath) / f
                    t = p.stat().st_mtime
                    if t > best_t:
                        best, best_t = p, t
    return best


def find_protocols(prefix: Path) -> dict[str, str]:
    """{scheme: windows command line} for every link type programs registered."""
    root, found = wine_root(prefix), {}
    for reg in ("system.reg", "user.reg"):          # HKLM, then HKCU (wins)
        secs = registry.read_sections(root / reg)
        for key, values in secs.items():
            m = re.fullmatch(r"software\\classes\\([a-z][a-z0-9+.\-]*)", key)
            if not m or "url protocol" not in values:
                continue
            scheme = m.group(1)
            cmd = secs.get(key + "\\shell\\open\\command", {}).get("@", "")
            if (cmd and scheme not in _PROTOCOL_BLOCK and not scheme.startswith("ms-")
                    and "\\windows\\" not in cmd.lower()):    # skip Wine's built-ins
                found[scheme] = cmd
    for scheme, exe_name in KNOWN_HANDLERS.items():
        if scheme not in found:
            exe = _find_newest(drive_c(prefix), exe_name)
            if exe:
                rel = exe.relative_to(drive_c(prefix))
                found[scheme] = '"C:\\' + str(rel).replace("/", "\\") + '" "%1"'
    return found


def win_to_linux(prefix: Path, winpath: str) -> Path:
    """C:\\Program Files\\X\\a.exe -> real path (case-insensitive, like Windows)."""
    m = re.match(r"^([a-zA-Z]):[\\/]?(.*)$", winpath)
    if not m:
        return Path(winpath)
    cur = (wine_root(prefix) / "dosdevices" / f"{m.group(1).lower()}:").resolve()
    for part in filter(None, re.split(r"[\\/]", m.group(2))):
        nxt = cur / part
        if not nxt.exists() and cur.is_dir():
            match = next((c for c in cur.iterdir() if c.name.lower() == part.lower()), None)
            nxt = match or nxt
        cur = nxt
    return cur


def split_command(cmd: str, url: str) -> list[str]:
    """'"C:\\x\\a.exe" "%1"' -> ['C:\\x\\a.exe', url]."""
    tokens = [a if a else b for a, b in re.findall(r'"([^"]*)"|(\S+)', cmd)]
    return [re.sub(r"%[1lL]", lambda _: url, t) for t in tokens]


def _protocol_desktop(app: core.App, scheme: str) -> Path:
    return DESKTOP_DIR / f"froccs-{app.id}-url-{scheme}.desktop"


def register_protocols(app: core.App) -> list[str]:
    schemes = sorted(find_protocols(app.prefix))
    for old in set(app.protocols) - set(schemes):
        _protocol_desktop(app, old).unlink(missing_ok=True)
    DESKTOP_DIR.mkdir(parents=True, exist_ok=True)
    for scheme in schemes:
        f = _protocol_desktop(app, scheme)
        f.write_text("\n".join([
            "[Desktop Entry]", "Type=Application",
            f"Name={' '.join(app.name.split())} ({scheme} links)",
            f"Exec={core.launcher_command()} url {app.id} %u",
            f"MimeType=x-scheme-handler/{scheme};",
            "NoDisplay=true", ""]))
        if shutil.which("xdg-mime"):
            subprocess.run(["xdg-mime", "default", f.name, f"x-scheme-handler/{scheme}"],
                           capture_output=True)
    app.protocols = schemes
    return schemes


def url_command(app: core.App, url: str) -> list[str]:
    """The registry is read again now: Fusion moves on every update."""
    scheme = url.split(":", 1)[0].lower()
    cmd = find_protocols(app.prefix).get(scheme)
    if not cmd:
        raise core.InstallError(f"{app.name} has no handler for '{scheme}:' links")
    args = split_command(cmd, url)
    return [str(win_to_linux(app.prefix, args[0])), *args[1:]]


def _clean_url(url: str) -> str:
    return url.strip().replace("&amp;", "&")     # copy-pasted links get mangled


def launch_url(app: core.App, url: str) -> None:
    url = _clean_url(url)
    with open(app.log_file, "a") as lf:
        lf.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')}  link received: "
                 f"{url.split(':', 1)[0]}: ({len(url)} chars)\n")
    if shutil.which("notify-send"):
        subprocess.Popen(["notify-send", "-i", APP_ID, app.name,
                          _("Login link received, passing it to the program…")])
    exe, *args = url_command(app, url)
    with open(app.log_file, "a") as lf:
        lf.write(f"    handler: {exe}\n")
    core.exec_in_prefix(app, exe, args)


def launch_url_detached(app: core.App, url: str) -> None:
    exe, *args = url_command(app, _clean_url(url))
    core.launch_detached(app, exe, args)


# ------------------------------------------------------------ lifecycle --

def rescan(app: core.App) -> bool:
    """Re-check login links and file types. True if something changed."""
    before = (list(app.protocols), [dict(f) for f in app.file_types])
    register_protocols(app)
    merge_detected_file_types(app)
    changed = before != (list(app.protocols), [dict(f) for f in app.file_types])
    if changed:
        write_desktop_files(app)
    return changed


def finalize(app: core.App, exes: list[Path]) -> None:
    """After install: first exe = main program, the rest = extra menu entries."""
    app.exe = str(exes[0])
    app.icon = extract_icon(exes[0], app.id)
    app.shortcuts = []
    for i, exe in enumerate(exes[1:]):
        name = re.sub(r"[_\-]+", " ", exe.stem)
        app.shortcuts.append({"name": name, "exe": str(exe),
                              "icon": extract_icon(exe, f"{app.id}-s{i}")})
    register_protocols(app)
    merge_detected_file_types(app)
    write_desktop_files(app)
    core.upsert_app(app)


def add_shortcut(app: core.App, exe: Path, name: str = "") -> None:
    i = len(app.shortcuts)
    app.shortcuts.append({"name": name or re.sub(r"[_\-]+", " ", exe.stem), "exe": str(exe),
                          "icon": extract_icon(exe, f"{app.id}-s{i}")})


def remove_shortcut(app: core.App, index: int) -> None:
    s = app.shortcuts.pop(index)
    if s.get("icon"):
        Path(s["icon"]).unlink(missing_ok=True)
    # file types pointing at it go back to the main program; indexes shift
    for ft in app.file_types:
        if ft.get("shortcut", -1) == index:
            ft["shortcut"] = -1
        elif ft.get("shortcut", -1) > index:
            ft["shortcut"] -= 1


def remove_desktop_integration(app: core.App) -> None:
    for f in DESKTOP_DIR.glob(f"froccs-{app.id}*.desktop"):
        f.unlink()
    (AUTOSTART_DIR / f"froccs-{app.id}.desktop").unlink(missing_ok=True)
    pkg = MIME_DIR / "packages" / f"froccs-{app.id}.xml"
    if pkg.exists():
        pkg.unlink()
        _update_mime_db()
    for f in ICON_DIR.glob(f"{app.id}*.png"):
        f.unlink()
    refresh_menu()


def remove_app(app: core.App) -> None:
    from . import backup
    core.kill_prefix(app.prefix)
    remove_desktop_integration(app)
    shutil.rmtree(app.prefix, ignore_errors=True)
    backup.delete_all_snapshots(app)
    core.save_apps([a for a in core.load_apps() if a.id != app.id])
