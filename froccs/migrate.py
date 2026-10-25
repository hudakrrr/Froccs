"""
One-time move from the old name (versions before 0.5) to Fröccs, and removal
of everything left over from it.

1. ~/.local/share/winshelf -> ~/.local/share/froccs. A rename on the same disk
   is instant and keeps every program, restore point and template.
2. Stored paths that contained the old folder are rewritten (apps.json,
   restore-point metadata, and absolute symlinks inside the prefixes).
3. Every trace of the old name is removed from the desktop: menu entries,
   login-link and file-type handlers, autostart entries, file types, icons,
   and default-app settings that pointed at them. The caller then recreates
   them under the new name (`froccs refresh`).
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

from .devices import xdg_dir
from .paths import (AUTOSTART_DIR, DATA_DIR, DESKTOP_DIR, HOME, MIME_DIR, OLD_APP_ID,
                    OLD_DATA_DIR, OLD_NAME, SNAPSHOT_DIR, TEMPLATE_DIR)

ICON_THEME = HOME / ".local" / "share" / "icons" / "hicolor"
MIMEAPPS = [Path(os.environ.get("XDG_CONFIG_HOME") or HOME / ".config") / "mimeapps.list",
            DESKTOP_DIR / "mimeapps.list"]


class MigrationError(RuntimeError):
    pass


def running_old_programs() -> list[str]:
    """Names of prefixes under the old folder that have processes running."""
    want = (str(OLD_DATA_DIR) + "/prefixes/").encode()
    found = set()
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            env = Path(f"/proc/{entry}/environ").read_bytes()
        except OSError:
            continue
        for var in env.split(b"\0"):
            if var.startswith(b"WINEPREFIX=") and var[11:].startswith(want):
                found.add(var[11 + len(want):].split(b"/")[0].decode(errors="replace"))
    return sorted(found)


def _rewrite_text_files(old: str, new: str) -> None:
    files = [DATA_DIR / "apps.json"] + list(SNAPSHOT_DIR.glob("*/*/meta.json"))
    for f in files:
        try:
            text = f.read_text()
        except OSError:
            continue
        fixed = text.replace(old + "/", new + "/").replace(f'"{old}"', f'"{new}"')
        if fixed != text:
            f.write_text(fixed)


def _rewrite_symlinks(old: str, new: str) -> int:
    count = 0
    for dirpath, dirnames, files in os.walk(DATA_DIR):
        for name in dirnames + files:
            p = os.path.join(dirpath, name)
            if os.path.islink(p):
                target = os.readlink(p)
                if target == old or target.startswith(old + "/"):
                    os.unlink(p)
                    os.symlink(new + target[len(old):], p)
                    count += 1
    return count


def _clean_mimeapps() -> None:
    """Drop default-app entries that point at the old .desktop files."""
    old_entry = re.compile(rf"^({OLD_NAME}-.*|" + re.escape(OLD_APP_ID) + r")\.desktop$")
    for f in MIMEAPPS:
        if not f.exists():
            continue
        out = []
        for line in f.read_text().splitlines():
            if "=" in line and not line.startswith("["):
                key, val = line.split("=", 1)
                keep = [v for v in val.split(";") if v and not old_entry.match(v)]
                if not keep:
                    continue
                line = f"{key}={';'.join(keep)};"
            out.append(line)
        f.write_text("\n".join(out) + "\n")


def remove_old_desktop_traces() -> None:
    for f in DESKTOP_DIR.glob(f"{OLD_NAME}-*.desktop"):
        f.unlink()
    (DESKTOP_DIR / f"{OLD_APP_ID}.desktop").unlink(missing_ok=True)
    for f in AUTOSTART_DIR.glob(f"{OLD_NAME}-*.desktop"):
        f.unlink()
    pkgs = MIME_DIR / "packages"
    for f in list(pkgs.glob(f"{OLD_NAME}-*.xml")) + [pkgs / f"{OLD_NAME}.xml"]:
        f.unlink(missing_ok=True)
    for f in ICON_THEME.glob(f"*/apps/{OLD_APP_ID}.png"):
        f.unlink()
    _clean_mimeapps()
    for cmd in (["update-mime-database", str(MIME_DIR)],
                ["update-desktop-database", str(DESKTOP_DIR)],
                ["gtk-update-icon-cache", "-f", "-t", str(ICON_THEME)],
                ["gtk4-update-icon-cache", "-f", "-t", str(ICON_THEME)]):
        if shutil.which(cmd[0]):
            subprocess.run(cmd, capture_output=True)


def migrate() -> list[str]:
    """Safe to run any number of times. Returns what it did."""
    done = []
    if OLD_DATA_DIR.is_dir() and not DATA_DIR.exists():
        busy = running_old_programs()
        if busy:
            raise MigrationError("Close these programs first, then run the update again: "
                                 + ", ".join(busy))
        OLD_DATA_DIR.rename(DATA_DIR)
        done.append(f"moved your programs to {DATA_DIR}")
        _rewrite_text_files(str(OLD_DATA_DIR), str(DATA_DIR))
        n = _rewrite_symlinks(str(OLD_DATA_DIR), str(DATA_DIR))
        if n:
            done.append(f"fixed {n} link(s) inside the programs")
    had_old = (any(DESKTOP_DIR.glob(f"{OLD_NAME}-*.desktop"))
               or (DESKTOP_DIR / f"{OLD_APP_ID}.desktop").exists()
               or any(ICON_THEME.glob(f"*/apps/{OLD_APP_ID}.png")))
    if had_old:
        remove_old_desktop_traces()
        done.append("removed the old name from the apps menu")
    done += remove_leftovers()
    return done


def _looks_like_old_source(folder: Path) -> bool:
    """An extracted release folder of the old name (install.sh + package)."""
    return (folder / "install.sh").is_file() and (folder / OLD_NAME / "__init__.py").is_file()


def remove_leftovers() -> list[str]:
    """Everything else the old name left behind. Never touches installed programs."""
    done = []
    old_bin = HOME / ".local" / "bin" / OLD_NAME
    if old_bin.exists():
        old_bin.unlink()
        done.append(f"removed the {OLD_NAME} command")
    shutil.rmtree(HOME / ".local" / "lib" / OLD_NAME, ignore_errors=True)
    # Ready-made environments: the marker file had the old name in it
    for marker in TEMPLATE_DIR.glob(f"*/.{OLD_NAME}-ready"):
        marker.rename(marker.with_name(".froccs-ready"))
    # Old data folder: only if it was already moved (otherwise migrate() keeps it)
    if OLD_DATA_DIR.is_dir() and DATA_DIR.is_dir():
        old_prefixes = OLD_DATA_DIR / "prefixes"
        has_programs = old_prefixes.is_dir() and any(old_prefixes.iterdir())
        if has_programs:
            done.append(f"NOTE: {OLD_DATA_DIR} still has programs in it and was left alone")
        else:
            shutil.rmtree(OLD_DATA_DIR, ignore_errors=True)
            done.append(f"removed the empty {OLD_DATA_DIR}")
    # Release files and extracted folders where downloads usually land
    places = {p for p in (xdg_dir("DOWNLOAD"), xdg_dir("DOCUMENTS"), HOME / "Downloads", HOME)
              if p and p.is_dir()}
    for place in places:
        for f in place.glob(f"{OLD_NAME}-*.tar.gz"):
            f.unlink()
            done.append(f"deleted {f}")
        folder = place / OLD_NAME
        if folder.is_dir() and _looks_like_old_source(folder):
            shutil.rmtree(folder, ignore_errors=True)
            done.append(f"deleted the old program folder {folder}")
    return done
