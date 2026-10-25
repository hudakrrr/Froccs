"""
Disk usage and clean-up.

Note on Btrfs: restore points and new apps share data with what they were
copied from (reflinks), but `du` counts shared data in full each time. So
the numbers are an upper limit; the real total on disk is usually lower.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from . import backup, core
from .paths import COMPAT_TOOLS, HOME, LOG_DIR, TEMPLATE_DIR, drive_c

UMU_RUNTIME = HOME / ".local" / "share" / "umu"
WINETRICKS_CACHE = HOME / ".cache" / "winetricks"


def size_of(path: Path) -> int:
    if not path.exists():
        return 0
    r = subprocess.run(["du", "-sb", str(path)], capture_output=True, text=True)
    try:
        return int(r.stdout.split()[0])
    except (IndexError, ValueError):
        return 0


def human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n} B"


def _temp_dirs(app: core.App) -> list[Path]:
    root = drive_c(app.prefix)
    out = [root / "windows" / "temp"]
    users = root / "users"
    if users.is_dir():
        for u in users.iterdir():
            out += [u / "AppData" / "Local" / "Temp", u / "Temp"]
    return [d for d in out if d.is_dir() and not d.is_symlink()]


def app_usage(app: core.App) -> dict:
    snaps = backup.list_snapshots(app)
    return {
        "program": size_of(app.prefix),
        "temp": sum(size_of(d) for d in _temp_dirs(app)),
        "snapshots": sum(size_of(Path(s["path"])) for s in snaps),
        "auto_snapshots": sum(size_of(Path(s["path"])) for s in snaps if s.get("auto")),
    }


def shared_usage() -> dict:
    return {
        "templates": size_of(TEMPLATE_DIR),
        "proton": size_of(COMPAT_TOOLS),
        "runtime": size_of(UMU_RUNTIME),
        "downloads_cache": size_of(WINETRICKS_CACHE),
        "logs": size_of(LOG_DIR),
    }


def _empty(folder: Path) -> None:
    for item in folder.iterdir():
        if item.is_dir() and not item.is_symlink():
            shutil.rmtree(item, ignore_errors=True)
        else:
            try:
                item.unlink()
            except OSError:
                pass


def clean_temp(app: core.App) -> None:
    if core.is_running(app):
        raise core.InstallError("Close the program first.")
    for d in _temp_dirs(app):
        _empty(d)


def clean_auto_snapshots(app: core.App) -> None:
    for s in backup.list_snapshots(app):
        if s.get("auto"):
            backup.delete_snapshot(s)


def clean_downloads_cache() -> None:
    """Installers winetricks downloaded (.NET etc.). Only needed to rebuild templates."""
    if WINETRICKS_CACHE.is_dir():
        shutil.rmtree(WINETRICKS_CACHE, ignore_errors=True)


def clean_logs() -> None:
    if LOG_DIR.is_dir():
        _empty(LOG_DIR)


def rebuild_templates() -> None:
    """Throw the templates away; they're rebuilt (in the background) when needed."""
    for key in core.TEMPLATES:
        shutil.rmtree(TEMPLATE_DIR / key, ignore_errors=True)
