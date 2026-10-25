"""
Proton versions: which ones are installed, which one an app uses, downloads.

Builds live in ~/.local/share/Steam/compatibilitytools.d (Steam and umu
look there too). An app either follows "latest" (newest installed GE-Proton)
or is pinned to one exact build, so an update can't break a working setup.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import tarfile
import tempfile
import urllib.request
from pathlib import Path
from typing import Callable, Optional

from .paths import COMPAT_TOOLS

GE_API = "https://api.github.com/repos/GloriousEggroll/proton-ge-custom/releases/latest"


def _version_key(name: str) -> list[int]:
    return [int(n) for n in re.findall(r"\d+", name)]


def installed() -> list[str]:
    """Names of installed Proton builds, newest first."""
    if not COMPAT_TOOLS.is_dir():
        return []
    names = [p.name for p in COMPAT_TOOLS.iterdir() if (p / "proton").exists()]
    return sorted(names, key=lambda n: (n.startswith("GE-Proton"), _version_key(n)),
                  reverse=True)


def newest_ge() -> Optional[str]:
    ge = [n for n in installed() if n.startswith("GE-Proton")]
    return ge[0] if ge else None


def resolve(pinned: str = "") -> str:
    """
    Value for PROTONPATH. A pinned build that still exists wins. Otherwise the
    newest local GE-Proton. Only when none is installed yet do we pass
    'GE-Proton', which makes umu download the newest one (needs internet).
    """
    if pinned and (COMPAT_TOOLS / pinned / "proton").exists():
        return str(COMPAT_TOOLS / pinned)
    ge = newest_ge()
    return str(COMPAT_TOOLS / ge) if ge else "GE-Proton"


def effective_name(pinned: str = "") -> str:
    p = resolve(pinned)
    return Path(p).name if "/" in p else p


def latest_release() -> dict:
    """{'name', 'url', 'sha_url', 'size'} of the newest GE-Proton on GitHub."""
    req = urllib.request.Request(GE_API, headers={"Accept": "application/vnd.github+json",
                                                  "User-Agent": "Fröccs"})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = json.load(r)
    tar = next(a for a in data["assets"] if a["name"].endswith(".tar.gz"))
    sha = next((a for a in data["assets"] if a["name"].endswith(".sha512sum")), None)
    return {"name": data["tag_name"], "url": tar["browser_download_url"],
            "sha_url": sha["browser_download_url"] if sha else None,
            "size": tar["size"]}


def download(release: dict, progress: Callable[[str, float], None]) -> str:
    """Download + verify + unpack a GE-Proton release. Returns its name."""
    COMPAT_TOOLS.mkdir(parents=True, exist_ok=True)
    if (COMPAT_TOOLS / release["name"] / "proton").exists():
        return release["name"]
    with tempfile.TemporaryDirectory(dir=COMPAT_TOOLS.parent) as tmp:
        tmp = Path(tmp)
        archive = tmp / "proton.tar.gz"
        h = hashlib.sha512()
        req = urllib.request.Request(release["url"], headers={"User-Agent": "Fröccs"})
        with urllib.request.urlopen(req, timeout=60) as r, open(archive, "wb") as f:
            total = int(r.headers.get("Content-Length") or release.get("size") or 0)
            done = 0
            while chunk := r.read(1 << 20):
                f.write(chunk)
                h.update(chunk)
                done += len(chunk)
                if total:
                    progress(f"{done >> 20} / {total >> 20} MB", 0.9 * done / total)
        if release.get("sha_url"):
            req = urllib.request.Request(release["sha_url"], headers={"User-Agent": "Fröccs"})
            with urllib.request.urlopen(req, timeout=20) as r:
                expected = r.read().decode().split()[0]
            if expected.lower() != h.hexdigest():
                raise RuntimeError("Download is damaged (checksum mismatch). Try again.")
        progress("Unpacking", 0.92)
        with tarfile.open(archive) as t:
            if hasattr(tarfile, "tar_filter"):
                t.extractall(tmp / "x", filter="tar")   # blocks unsafe paths
            else:                                       # older Python: check by hand
                root = (tmp / "x").resolve()
                for m in t.getmembers():
                    if not (root / m.name).resolve().is_relative_to(root):
                        raise RuntimeError(f"Unsafe path in archive: {m.name}")
                t.extractall(tmp / "x")
        top = next((tmp / "x").iterdir())
        shutil.move(str(top), COMPAT_TOOLS / release["name"])
    progress("Done", 1.0)
    return release["name"]


def remove(name: str) -> None:
    target = COMPAT_TOOLS / name
    if target.parent == COMPAT_TOOLS and target.is_dir():
        shutil.rmtree(target)
