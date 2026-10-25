"""
Updating Fröccs from inside the app.

Until Fröccs is published there's no update server, so we look for newer
release files (froccs-X.Y.Z.tar.gz) where downloads usually land and run
their install.sh, the same way as by hand. Installed programs are never
touched by an update (they live in ~/.local/share/froccs).
"""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from . import __version__
from .devices import xdg_dir
from .paths import HOME

PACKAGE = "froccs"
_NAME = re.compile(rf"^{PACKAGE}-(\d+(?:\.\d+)*)\.tar\.gz$")


def version_tuple(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v))


def search_dirs() -> list[Path]:
    dirs = [xdg_dir("DOWNLOAD"), xdg_dir("DOCUMENTS"), HOME / "Downloads", HOME]
    out = []
    for d in dirs:
        if d and d.is_dir() and d not in out:
            out.append(d)
    return out


def find_update() -> Optional[tuple[str, Path]]:
    """Newest release file that is newer than the running version."""
    best: Optional[tuple[str, Path]] = None
    for d in search_dirs():
        try:
            entries = list(d.iterdir())
        except OSError:
            continue
        for f in entries:
            m = _NAME.match(f.name)
            if m and version_tuple(m.group(1)) > version_tuple(__version__):
                if best is None or version_tuple(m.group(1)) > version_tuple(best[0]):
                    best = (m.group(1), f)
    return best


def file_version(path: Path) -> Optional[str]:
    m = _NAME.match(path.name)
    return m.group(1) if m else None


def install(path: Path) -> tuple[bool, str]:
    """Unpack the release and run its install.sh. Returns (ok, output)."""
    with tempfile.TemporaryDirectory() as tmp:
        r = subprocess.run(["tar", "-xzf", str(path), "-C", tmp], capture_output=True, text=True)
        if r.returncode != 0:
            return False, r.stderr
        script = next(Path(tmp).glob("*/install.sh"), None)
        if script is None:
            return False, "install.sh not found in this file"
        env = dict(os.environ, FROCCS_FROM_APP="1")
        r = subprocess.run(["bash", str(script)], cwd=script.parent, env=env,
                           capture_output=True, text=True, timeout=600)
        return r.returncode == 0, (r.stdout + r.stderr).strip()


def restart() -> None:
    """Start the new version a moment after this one has quit."""
    launcher = HOME / ".local" / "bin" / "froccs"
    subprocess.Popen(["sh", "-c", f'sleep 1; exec "{launcher}"'], start_new_session=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL)
