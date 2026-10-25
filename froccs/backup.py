"""
Restore points, export/import, and adopting existing Wine/Steam prefixes.

A restore point is a full copy of the app's prefix plus its settings. On
Btrfs the copy is a reflink: the files are shared until one side changes,
so a restore point costs almost no space or time until the app changes
things (only the changed parts take new space).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import asdict
from pathlib import Path
from typing import Callable, Optional

from . import core, devices, integration
from .paths import EXPORT_SUFFIX, HOME, ICON_DIR, PREFIX_DIR, SNAPSHOT_DIR, drive_c

MAX_AUTO = 3      # automatic restore points kept per app


# --------------------------------------------------------- restore points --

def _snap_root(app: core.App) -> Path:
    return SNAPSHOT_DIR / app.id


def list_snapshots(app: core.App) -> list[dict]:
    root = _snap_root(app)
    out = []
    if root.is_dir():
        for d in root.iterdir():
            meta = d / "meta.json"
            if meta.exists():
                try:
                    m = json.loads(meta.read_text())
                    m["path"] = str(d)
                    out.append(m)
                except json.JSONDecodeError:
                    pass
    return sorted(out, key=lambda m: m["time"], reverse=True)


def create_snapshot(app: core.App, label: str, auto: bool = False) -> dict:
    if core.is_running(app):
        raise core.InstallError("Close the program first: Windows keeps its settings "
                                "in memory while it runs, so a copy now would be incomplete.")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = _snap_root(app) / stamp
    while dest.exists():
        stamp += "x"
        dest = _snap_root(app) / stamp
    core.copy_tree(app.prefix, dest / "prefix")
    meta = {"label": label, "time": time.time(), "auto": auto, "app": asdict(app)}
    (dest / "meta.json").write_text(json.dumps(meta, indent=2))
    if auto:
        autos = [s for s in list_snapshots(app) if s.get("auto")]
        for old in autos[MAX_AUTO:]:
            shutil.rmtree(old["path"], ignore_errors=True)
    return meta


def restore_snapshot(app: core.App, snap: dict) -> core.App:
    """Put the app back exactly as it was (files and settings)."""
    core.kill_prefix(app.prefix)
    time.sleep(1)
    trash = app.prefix.with_name(app.prefix.name + ".old")
    shutil.rmtree(trash, ignore_errors=True)
    if app.prefix.exists():
        app.prefix.rename(trash)
    core.copy_tree(Path(snap["path"]) / "prefix", app.prefix)
    shutil.rmtree(trash, ignore_errors=True)
    known = core.App.__dataclass_fields__
    restored = core.App(**{k: v for k, v in snap["app"].items() if k in known})
    restored.id, restored.name, restored.icon = app.id, app.name, app.icon
    core.upsert_app(restored)
    integration.write_desktop_files(restored)
    return restored


def delete_snapshot(snap: dict) -> None:
    shutil.rmtree(snap["path"], ignore_errors=True)


def delete_all_snapshots(app: core.App) -> None:
    shutil.rmtree(_snap_root(app), ignore_errors=True)


def maybe_auto_snapshot(app: core.App, label: str) -> bool:
    if core.load_settings().get("auto_snapshots") and not core.is_running(app):
        try:
            create_snapshot(app, label, auto=True)
            return True
        except Exception:
            return False
    return False


# ---------------------------------------------------------- export/import --
#
# A .froccs file is a tar.zst archive: app.json + icons + the prefix.
# Paths inside the prefix contain the old home folder (for example symlinks to
# ~/Documents), so on import we rewrite them for the new machine.

def export_app(app: core.App, dest: Path, progress: Callable[[str, float], None]) -> None:
    if core.is_running(app):
        raise core.InstallError("Close the program before exporting it.")
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        meta = {"format": 1, "app": asdict(app), "prefix": str(app.prefix),
                "home": str(HOME)}
        (tmp / "app.json").write_text(json.dumps(meta, indent=2))
        icons = tmp / "icons"
        icons.mkdir()
        for f in ICON_DIR.glob(f"{app.id}*.png"):
            shutil.copy(f, icons / f.name)
        progress("Packing (this can take a few minutes for big programs)", 0.1)
        # zstd packs fast and small; gzip if it's missing. tar detects either on import.
        comp = "--zstd" if shutil.which("zstd") else "-z"
        cmd = ["tar", comp, "-cf", str(dest), "-C", str(tmp), "app.json", "icons",
               "-C", str(PREFIX_DIR), app.id]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            dest.unlink(missing_ok=True)
            raise core.InstallError(r.stderr.strip() or "Export failed")
    progress("Done", 1.0)


def _rebase(value, old: str, new: str):
    if isinstance(value, str):
        return value.replace(old, new) if value.startswith(old) else value
    if isinstance(value, list):
        return [_rebase(v, old, new) for v in value]
    if isinstance(value, dict):
        return {k: _rebase(v, old, new) for k, v in value.items()}
    return value


def _fix_symlinks(prefix: Path, old_home: str, new_home: str) -> None:
    if old_home == new_home:
        return
    for dirpath, dirnames, files in os.walk(prefix):
        for name in dirnames + files:
            p = Path(dirpath) / name
            if p.is_symlink():
                target = os.readlink(p)
                if target.startswith(old_home + "/") or target == old_home:
                    p.unlink()
                    p.symlink_to(new_home + target[len(old_home):])


def import_app(archive: Path, progress: Callable[[str, float], None]) -> core.App:
    PREFIX_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=PREFIX_DIR.parent) as tmp:  # same disk -> fast move
        tmp = Path(tmp)
        progress("Unpacking", 0.1)
        r = subprocess.run(["tar", "-xf", str(archive), "-C", str(tmp)],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise core.InstallError(r.stderr.strip() or "Not a Fröccs file")
        meta = json.loads((tmp / "app.json").read_text())
        old = core.App(**{k: v for k, v in meta["app"].items()
                          if k in core.App.__dataclass_fields__})
        new_id = core.make_id(old.name)
        new_prefix = PREFIX_DIR / new_id
        shutil.move(str(tmp / old.id), new_prefix)
        progress("Adapting to this computer", 0.8)
        data = _rebase(asdict(old), meta["prefix"], str(new_prefix))
        data = _rebase(data, str(Path(meta["prefix"]).parent.parent / "icons"), str(ICON_DIR))
        app = core.App(**data)
        app.id = new_id
        ICON_DIR.mkdir(parents=True, exist_ok=True)
        for f in (tmp / "icons").glob("*.png"):
            shutil.copy(f, ICON_DIR / f.name.replace(old.id, new_id, 1))
        app.icon = app.icon.replace(old.id, new_id) if app.icon else ""
        for s in app.shortcuts:
            if s.get("icon"):
                s["icon"] = s["icon"].replace(old.id, new_id)
    _fix_symlinks(app.prefix, meta.get("home", str(HOME)), str(HOME))
    _force_proton_refresh(app.prefix)
    devices.apply_all(app)
    integration.register_protocols(app)
    integration.write_desktop_files(app)
    core.upsert_app(app)
    progress("Done", 1.0)
    return app


def _force_proton_refresh(prefix: Path) -> None:
    """Proton re-links its own files into a prefix when its version marker
    differs. After moving a prefix those links may point to the wrong home."""
    for marker in (prefix / "version", prefix / "pfx" / "version"):
        marker.unlink(missing_ok=True)


def find_wine_root(folder: Path) -> Optional[Path]:
    for cand in (folder, folder / "pfx"):
        if (cand / "drive_c").is_dir() and (cand / "system.reg").exists():
            return folder
    return None


def import_prefix(folder: Path, name: str,
                  progress: Callable[[str, float], None]) -> tuple[core.App, list[Path]]:
    """
    Adopt an existing Wine prefix or Steam/Proton compatdata folder. It is
    COPIED (the original keeps working). Returns the new app (not yet saved)
    and candidate programs to choose from.
    """
    if find_wine_root(folder) is None:
        raise core.InstallError("That folder doesn't look like a Wine prefix "
                                "(no drive_c and system.reg inside).")
    app = core.App(id=core.make_id(name), name=name)
    progress("Copying (instant on Btrfs)", 0.2)
    core.copy_tree(folder, app.prefix)
    _force_proton_refresh(app.prefix)
    progress("Looking for programs", 0.8)
    exes = core.list_exes(app.prefix)
    app.dotnet = (drive_c(app.prefix) / "windows" / "Microsoft.NET" / "Framework64"
                  / "v4.0.30319" / "WPF").is_dir()
    return app, core.rank_candidates({}, exes, limit=20)
