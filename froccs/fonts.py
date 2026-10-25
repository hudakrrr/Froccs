"""
Installing fonts into a program's Windows environment.

Windows keeps fonts in C:\\Windows\\Fonts and lists them in the registry under
HKLM\\Software\\Microsoft\\Windows NT\\CurrentVersion\\Fonts as
"Font Name (TrueType)" = "file.ttf". Wine finds fonts in the folder by
itself, but some programs (CAD ones especially) read that registry list, so
we do both. The display name is read from the font file's own 'name' table.
"""
from __future__ import annotations

import shutil
import struct
from pathlib import Path

from . import core, registry
from .paths import drive_c, wine_root

FONT_KEY = "Software\\Microsoft\\Windows NT\\CurrentVersion\\Fonts"
FONT_PATTERNS = ["*.ttf", "*.otf", "*.ttc"]


def font_name(path: Path) -> str:
    """Full font name (name ID 4) from a .ttf/.otf file; file name as fallback."""
    try:
        data = path.read_bytes()
        if data[:4] == b"ttcf":
            return path.stem
        num_tables = struct.unpack_from(">H", data, 4)[0]
        for i in range(num_tables):
            tag, _cs, offset, _len = struct.unpack_from(">4sIII", data, 12 + 16 * i)
            if tag != b"name":
                continue
            _fmt, count, str_off = struct.unpack_from(">HHH", data, offset)
            found = {}
            for j in range(count):
                pid, eid, lid, nid, length, off = struct.unpack_from(
                    ">HHHHHH", data, offset + 6 + 12 * j)
                if nid not in (1, 4):
                    continue
                raw = data[offset + str_off + off: offset + str_off + off + length]
                if pid == 3:                                      # Windows: UTF-16BE
                    text = raw.decode("utf-16-be", errors="ignore")
                    if lid == 0x409 or (nid, "win") not in found:
                        found[(nid, "win")] = text
                elif pid == 1 and eid == 0:                       # Mac Roman
                    found.setdefault((nid, "mac"), raw.decode("mac_roman", errors="ignore"))
            for key in ((4, "win"), (4, "mac"), (1, "win"), (1, "mac")):
                if found.get(key, "").strip():
                    return found[key].strip()
    except (OSError, struct.error):
        pass
    return path.stem


def install_fonts(app: core.App, files: list[Path]) -> list[str]:
    if core.is_running(app):
        raise core.InstallError("Close the program first.")
    dest = drive_c(app.prefix) / "windows" / "Fonts"
    dest.mkdir(parents=True, exist_ok=True)
    sysreg = wine_root(app.prefix) / "system.reg"
    added = []
    for f in files:
        shutil.copy2(f, dest / f.name)
        kind = "OpenType" if f.suffix.lower() == ".otf" else "TrueType"
        name = f"{font_name(f)} ({kind})"
        if sysreg.exists():
            registry.set_string(sysreg, FONT_KEY, name, f.name)
        if f.name not in app.fonts:
            app.fonts.append(f.name)
        added.append(name)
    core.upsert_app(app)
    return added


def remove_font(app: core.App, filename: str) -> None:
    if core.is_running(app):
        raise core.InstallError("Close the program first.")
    (drive_c(app.prefix) / "windows" / "Fonts" / filename).unlink(missing_ok=True)
    sysreg = wine_root(app.prefix) / "system.reg"
    if sysreg.exists():
        for name, value in registry.get_values(sysreg, FONT_KEY).items():
            if value.lower() == filename.lower():
                registry.set_string(sysreg, FONT_KEY, name, None)
    if filename in app.fonts:
        app.fonts.remove(filename)
    core.upsert_app(app)
