"""
Reading and editing Wine's registry files directly.

Wine keeps the Windows registry as three text files in the prefix:
  system.reg  = HKEY_LOCAL_MACHINE
  user.reg    = HKEY_CURRENT_USER
  userdef.reg = defaults for new users
Each key is a section like  [Software\\\\Wine\\\\Ports] 1700000000  followed by
"Name"="value" lines. Editing the file is instant, while running Wine's own
`reg.exe` takes seconds, but only works when no program in the prefix is running
(the wineserver keeps the registry in memory and writes it back on exit).
"""
from __future__ import annotations

import re
import time
from pathlib import Path

_SECTION = re.compile(r"^\[(.+?)\](?: \d+)?$")
_VALUE = re.compile(r'^(@|"((?:[^"\\]|\\.)*)")=(.*)$')


def _unescape(s: str) -> str:
    return re.sub(r'\\(["\\])', r"\1", s)


def _escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _key_header(key: str) -> str:
    return "[" + key.replace("\\", "\\\\") + "]"


def read_sections(path: Path) -> dict[str, dict[str, str]]:
    """{lowercase key: {lowercase value name: string value}} (strings only)."""
    out: dict[str, dict[str, str]] = {}
    cur = None
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return out
    for line in text.split("\n"):
        m = _SECTION.match(line)
        if m:
            cur = out.setdefault(m.group(1).replace("\\\\", "\\").lower(), {})
            continue
        if cur is None or not line.startswith(('"', "@")):
            continue
        vm = _VALUE.match(line)
        if not vm:
            continue
        name = "@" if vm.group(1) == "@" else _unescape(vm.group(2)).lower()
        data = vm.group(3)
        sm = re.match(r'^(?:str\(2\):)?"((?:[^"\\]|\\.)*)"$', data)
        cur[name] = _unescape(sm.group(1)) if sm else data
    return out


def _load(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8", errors="surrogateescape").split("\n")


def _save(path: Path, lines: list[str]) -> None:
    path.write_text("\n".join(lines), encoding="utf-8", errors="surrogateescape")


def _find_section(lines: list[str], key: str) -> int | None:
    header = _key_header(key)
    low = header.lower()
    for i, line in enumerate(lines):
        if line.lower() == low or line.lower().startswith(low + " "):
            return i
    return None


def _section_end(lines: list[str], start: int) -> int:
    j = start + 1
    while j < len(lines) and not lines[j].startswith("["):
        j += 1
    # step back over trailing blank lines
    while j > start + 1 and lines[j - 1] == "":
        j -= 1
    return j


def set_value(path: Path, key: str, name: str | None, raw_value: str | None) -> None:
    """
    Set (or with raw_value=None delete) one value. `raw_value` is in .reg
    syntax: '"text"' for strings, 'dword:00000078' for numbers.
    name=None just makes sure the key exists.
    """
    lines = _load(path)
    start = _find_section(lines, key)
    if start is None:
        if raw_value is None and name is not None:
            return
        lines += ["", f"{_key_header(key)} {int(time.time())}"]
        start = len(lines) - 1
    if name is None:
        _save(path, lines)
        return
    vname = "@" if name == "@" else f'"{_escape(name)}"'
    end = _section_end(lines, start)
    for i in range(start + 1, end):
        if lines[i].lower().startswith(vname.lower() + "="):
            if raw_value is None:
                del lines[i]
            else:
                lines[i] = f"{vname}={raw_value}"
            _save(path, lines)
            return
    if raw_value is not None:
        insert = start + 1
        while insert < end and lines[insert].startswith("#"):   # metadata lines
            insert += 1
        lines.insert(insert, f"{vname}={raw_value}")
        _save(path, lines)


def set_string(path: Path, key: str, name: str, value: str | None) -> None:
    set_value(path, key, name, None if value is None else f'"{_escape(value)}"')


def set_dword(path: Path, key: str, name: str, value: int) -> None:
    set_value(path, key, name, f"dword:{value:08x}")


def get_values(path: Path, key: str) -> dict[str, str]:
    return read_sections(path).get(key.lower(), {})
