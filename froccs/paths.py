"""Where Fröccs keeps things, plus small helpers for finding parts of a prefix."""
from __future__ import annotations

import os
from pathlib import Path

HOME = Path.home()
_XDG_DATA = Path(os.environ.get("XDG_DATA_HOME") or HOME / ".local" / "share")
_XDG_CONFIG = Path(os.environ.get("XDG_CONFIG_HOME") or HOME / ".config")

DATA_DIR = _XDG_DATA / "froccs"
PREFIX_DIR = DATA_DIR / "prefixes"        # one Windows environment per app
TEMPLATE_DIR = DATA_DIR / "templates"     # ready-made environments to copy
SNAPSHOT_DIR = DATA_DIR / "snapshots"     # restore points
ICON_DIR = DATA_DIR / "icons"
LOG_DIR = DATA_DIR / "logs"
DB_FILE = DATA_DIR / "apps.json"
SETTINGS_FILE = DATA_DIR / "settings.json"

DESKTOP_DIR = _XDG_DATA / "applications"
MIME_DIR = _XDG_DATA / "mime"
AUTOSTART_DIR = _XDG_CONFIG / "autostart"
LAUNCHER = HOME / ".local" / "bin" / "froccs"
COMPAT_TOOLS = _XDG_DATA / "Steam" / "compatibilitytools.d"   # where umu keeps Proton builds

APP_ID = "io.github.froccs.Froccs"
EXPORT_SUFFIX = ".froccs"

# Where versions before 0.5 kept things (only used by migrate.py)
OLD_NAME = "winshelf"
OLD_DATA_DIR = _XDG_DATA / OLD_NAME
OLD_APP_ID = "io.github.winshelf.WinShelf"


def wine_root(prefix: Path) -> Path:
    """Folder holding drive_c and the .reg files (Steam-style prefixes add pfx/)."""
    for cand in (prefix, prefix / "pfx"):
        if (cand / "drive_c").is_dir():
            return cand
    return prefix


def drive_c(prefix: Path) -> Path:
    return wine_root(prefix) / "drive_c"


def dosdevices(prefix: Path) -> Path:
    return wine_root(prefix) / "dosdevices"


def windows_user_dir(prefix: Path) -> Path | None:
    """drive_c/users/<user> - 'steamuser' under Proton."""
    users = drive_c(prefix) / "users"
    if not users.is_dir():
        return None
    for d in sorted(users.iterdir()):
        if d.is_dir() and d.name.lower() not in ("public", "default"):
            return d
    return None
