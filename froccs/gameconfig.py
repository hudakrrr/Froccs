"""
Tell games the screen's real resolution and refresh rate.

Games don't ask Windows "what does the screen do?" every time they start. They
remember their own setting in a config file. Under Wine, that remembered
setting goes wrong often: the first start happened at a moment when the
window was on the other screen, at 60 Hz, or it saved the whole desktop
(both monitors side by side). Then every start uses the bad value again.

So before each start we fix the setting in the files that the common game
engines write. We set only resolution, refresh rate and monitor. Graphics
quality, window mode and the rest stay as they are, and we only touch files the
game has already made:

* World of Warcraft (all editions): WTF/Config.wtf
  gxFullscreenResolution, gxRefresh and, if a screen is chosen, gxMonitor
* Unreal Engine 4/5 games: AppData/Local/<game>/Saved/Config/<platform>/
  GameUserSettings.ini  ResolutionSize*, LastUserConfirmedResolutionSize*,
  DesiredScreen*, LastUserConfirmedDesiredScreen*
* Unity games: registry HKCU\\Software\\<company>\\<game>
  "Screenmanager Resolution Width/Height_h<number>"

The screen is the one chosen in Settings → General → Screen. If none is
chosen, it's the screen the game itself is set to (if it says), else the main
screen. The sizes are the ones X11 programs see, which is what Wine hands
the game, so the game always gets a mode it can actually use.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from .paths import drive_c, windows_user_dir


def _monitors():
    from . import placement
    return placement.monitors()


def wine_order(mons) -> list:
    """Screens in the order Windows programs see them: main screen first."""
    return sorted(mons, key=lambda m: (not m.primary, m.index))


def _target(app, mons, game_index: Optional[int] = None):
    order = wine_order(mons)
    if not order:
        return None
    if app.screen:
        chosen = next((m for m in order if m.name == app.screen), None)
        if chosen:
            return chosen
    if game_index is not None and 0 <= game_index < len(order):
        return order[game_index]
    return order[0]


def _hz(mon) -> int:
    return int(round(mon.refresh)) if mon.refresh else 0


# ------------------------------------------------------- World of Warcraft --

_WTF_LINE = re.compile(r'^(\s*SET\s+)(\w+)(\s+)"([^"]*)"(.*)$', re.IGNORECASE)


def _read(path: Path) -> str:
    # newline="": keep the file's own line endings (WoW writes Windows ones)
    with open(path, encoding="utf-8", errors="surrogateescape", newline="") as f:
        return f.read()


def _write(path: Path, text: str) -> None:
    with open(path, "w", encoding="utf-8", errors="surrogateescape", newline="") as f:
        f.write(text)


def _wow_configs(prefix: Path) -> list[Path]:
    c = drive_c(prefix)
    out = []
    for pattern in ("*/*/WTF/Config.wtf", "*/*/*/WTF/Config.wtf", "*/*/*/*/WTF/Config.wtf"):
        out += c.glob(pattern)
    return sorted(set(out))


def fix_wow(path: Path, app, mons) -> bool:
    try:
        text = _read(path)
    except OSError:
        return False
    eol = "\r\n" if "\r\n" in text else "\n"
    lines = text.split(eol)
    found: dict[str, int] = {}
    for i, line in enumerate(lines):
        m = _WTF_LINE.match(line)
        if m:
            found[m.group(2).lower()] = i
    game_index = None
    if "gxmonitor" in found:
        try:
            game_index = int(_WTF_LINE.match(lines[found["gxmonitor"]]).group(4))
        except ValueError:
            pass
    mon = _target(app, mons, game_index)
    if not mon:
        return False
    want = {"gxFullscreenResolution": f"{mon.width}x{mon.height}"}
    if _hz(mon):
        want["gxRefresh"] = str(_hz(mon))
    if app.screen and len(mons) > 1:
        want["gxMonitor"] = str(wine_order(mons).index(mon))
    while lines and lines[-1].strip() == "":
        lines.pop()
    changed = False
    for key, value in want.items():
        i = found.get(key.lower())
        if i is None:
            lines.append(f'SET {key} "{value}"')
            changed = True
            continue
        m = _WTF_LINE.match(lines[i])
        if m.group(4) != value:
            lines[i] = f'{m.group(1)}{m.group(2)}{m.group(3)}"{value}"{m.group(5)}'
            changed = True
    if changed:
        _write(path, eol.join(lines) + eol)
    return changed


# ------------------------------------------------------------ Unreal Engine --

_UE_KEYS = {
    "resolutionsizex": "w", "resolutionsizey": "h",
    "lastuserconfirmedresolutionsizex": "w", "lastuserconfirmedresolutionsizey": "h",
    "desiredscreenwidth": "w", "desiredscreenheight": "h",
    "lastuserconfirmeddesiredscreenwidth": "w", "lastuserconfirmeddesiredscreenheight": "h",
}
_INI_LINE = re.compile(r"^(\s*)(\w+)(\s*=\s*)(-?\d+)(\s*)$")


def _unreal_configs(prefix: Path) -> list[Path]:
    user = windows_user_dir(prefix)
    if not user:
        return []
    local = user / "AppData" / "Local"
    return sorted(local.glob("*/Saved/Config/*/GameUserSettings.ini")) if local.is_dir() else []


def fix_unreal(path: Path, app, mons) -> bool:
    mon = _target(app, mons)
    if not mon:
        return False
    try:
        raw = path.read_bytes()
    except OSError:
        return False
    # Unreal writes UTF-8, sometimes UTF-16 with a BOM
    enc = "utf-16" if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else "utf-8"
    lines = raw.decode(enc, errors="surrogateescape").split("\n")
    size = {"w": str(mon.width), "h": str(mon.height)}
    changed = False
    for i, line in enumerate(lines):
        cr = line.endswith("\r")
        m = _INI_LINE.match(line.rstrip("\r"))
        if not m or m.group(2).lower() not in _UE_KEYS:
            continue
        value = size[_UE_KEYS[m.group(2).lower()]]
        if m.group(4) != value:
            lines[i] = f"{m.group(1)}{m.group(2)}{m.group(3)}{value}{m.group(5)}" + ("\r" if cr else "")
            changed = True
    if changed:
        path.write_bytes("\n".join(lines).encode(enc, errors="surrogateescape"))
    return changed


# -------------------------------------------------------------------- Unity --

_UNITY = re.compile(r'^("Screenmanager Resolution (Width|Height)_h\d+")=dword:[0-9a-fA-F]{8}(\r?)$')


def fix_unity(prefix: Path, app, mons) -> bool:
    """Edits user.reg directly, so the program must not be running."""
    mon = _target(app, mons)
    reg = prefix / "user.reg"
    if not mon or not reg.exists():
        return False
    try:
        lines = _read(reg).split("\n")
    except OSError:
        return False
    changed = False
    for i, line in enumerate(lines):
        m = _UNITY.match(line)
        if not m:
            continue
        value = mon.width if m.group(2) == "Width" else mon.height
        new = f"{m.group(1)}=dword:{value:08x}{m.group(3)}"
        if line != new:
            lines[i] = new
            changed = True
    if changed:
        _write(reg, "\n".join(lines))
    return changed


# ---------------------------------------------------------------- launching --

def _skip(app) -> bool:
    if not getattr(app, "match_resolution", False):
        return True
    if getattr(app, "wayland", False):
        # Native Wayland: Wine sees the real pixels, X11's sizes may differ
        # (fractional scaling). Leave the game's own choice alone.
        from . import screens
        return any(abs(s - 1.0) > 0.01 for s, _p in screens.monitor_scales())
    return False


def apply_on_launch(app, running: bool = False) -> list[str]:
    """Before a start: fix the saved resolution. Returns the changed files."""
    if _skip(app):
        return []
    mons = _monitors()
    if not mons:
        return []
    changed = []
    for path in _wow_configs(app.prefix):
        if fix_wow(path, app, mons):
            changed.append(str(path))
    for path in _unreal_configs(app.prefix):
        if fix_unreal(path, app, mons):
            changed.append(str(path))
    if not running and fix_unity(app.prefix, app, mons):
        changed.append(str(app.prefix / "user.reg"))
    return changed
