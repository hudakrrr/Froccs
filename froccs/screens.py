"""
Automatic display scaling: pick the Windows scaling that matches the screen.

Supported: GNOME (below), KDE Plasma (kscreen-doctor + kwinrc), other Wayland
desktops (they stretch X11 apps themselves), and X11 sessions (Xft.dpi).

GNOME tells other programs about its screens over D-Bus (the desktop's
internal message bus): org.gnome.Mutter.DisplayConfig.GetCurrentState
returns every "logical monitor" with its scale (1.0, 1.25, 2.0 ...).

The catch is WHO does the scaling. Wine draws through XWayland (an X11
server inside the Wayland desktop). By default GNOME draws X11 apps at 100 %
and then stretches them up itself (bigger but slightly blurry). If Windows
scaling were 125 % on top of that, everything would be 125 % x 125 % = too
big. Only when the "xwayland-native-scaling" feature is on does GNOME hand
X11 apps the real pixels and expect them to scale themselves.
"""
from __future__ import annotations

import os
from typing import Optional


def _gio():
    import gi
    gi.require_version("Gio", "2.0")
    from gi.repository import Gio
    return Gio


def _desktop() -> str:
    return os.environ.get("XDG_CURRENT_DESKTOP", "").lower()


def _wayland() -> bool:
    return os.environ.get("XDG_SESSION_TYPE") == "wayland"


def _kde_scales() -> list[tuple[float, bool]]:
    """KDE Plasma: kscreen-doctor -j lists outputs with scale and priority."""
    import json
    import subprocess
    try:
        r = subprocess.run(["kscreen-doctor", "-j"], capture_output=True, text=True, timeout=3)
        outs = [o for o in json.loads(r.stdout).get("outputs", []) if o.get("enabled")]
    except Exception:
        return []
    return [(float(o.get("scale", 1.0)),
             bool(o.get("primary")) or o.get("priority") == 1) for o in outs]


def _x11_scales() -> list[tuple[float, bool]]:
    """X11 sessions (Cinnamon, Xfce, MATE...): the desktop publishes Xft.dpi."""
    import subprocess
    try:
        r = subprocess.run(["xrdb", "-query"], capture_output=True, text=True, timeout=3)
    except Exception:
        return []
    for line in r.stdout.splitlines():
        if line.startswith("Xft.dpi:"):
            try:
                return [(float(line.split(":", 1)[1]) / 96, True)]
            except ValueError:
                pass
    return [(1.0, True)] if r.returncode == 0 else []


def _kde_x11_apps_scale_themselves() -> bool:
    """Plasma: System Settings → Display → 'Legacy applications (X11)'.
    'Apply scaling themselves' (default) = kwinrc [Xwayland] XwaylandClientsScale=true."""
    import configparser
    cfg = configparser.ConfigParser(strict=False, interpolation=None)
    path = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
                        "kwinrc")
    try:
        cfg.read(path)
        return cfg.get("Xwayland", "XwaylandClientsScale", fallback="true").lower() != "false"
    except configparser.Error:
        return True


def monitor_scales() -> list[tuple[float, bool]]:
    """[(scale, is_primary)] for each screen; [] if the desktop didn't tell us."""
    if "kde" in _desktop():
        return _kde_scales()
    if not _wayland():
        return _x11_scales()
    try:
        Gio = _gio()
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        res = bus.call_sync("org.gnome.Mutter.DisplayConfig", "/org/gnome/Mutter/DisplayConfig",
                            "org.gnome.Mutter.DisplayConfig", "GetCurrentState", None, None,
                            Gio.DBusCallFlags.NONE, 2000, None)
        _serial, _monitors, logical, _props = res.unpack()
        return [(float(lm[2]), bool(lm[4])) for lm in logical]
    except Exception:
        return []


def primary_scale() -> Optional[float]:
    scales = monitor_scales()
    if not scales:
        return None
    return next((s for s, primary in scales if primary), scales[0][0])


def desktop_scales_x11_apps() -> bool:
    """True when the desktop stretches X11 (XWayland) apps itself."""
    if not _wayland():
        return False                      # X11 session: apps scale themselves
    if "kde" in _desktop():
        return not _kde_x11_apps_scale_themselves()
    if "gnome" not in _desktop():
        return True                       # Sway, Hyprland...: compositor stretches them
    try:
        Gio = _gio()
        src = Gio.SettingsSchemaSource.get_default()
        if src is None or src.lookup("org.gnome.mutter", True) is None:
            return True
        features = Gio.Settings.new("org.gnome.mutter").get_strv("experimental-features")
        return "xwayland-native-scaling" not in features
    except Exception:
        return True


def auto_percent(app) -> Optional[int]:
    """The scaling Fröccs would use right now, or None if unknown."""
    if not app.wayland and desktop_scales_x11_apps():
        return 100                        # the desktop already enlarges the window
    scale = primary_scale()
    if scale is None:
        return None
    return max(100, int(round(scale * 100 / 5) * 5))


def apply_auto(app) -> bool:
    """Before a launch: switch the prefix to the screen's scaling if needed."""
    if not getattr(app, "scale_auto", False):
        return False
    from . import core
    pct = auto_percent(app)
    if pct is None or core.is_running(app):
        return False
    if all(v == pct for v in core.read_scale(app).values()):
        return False
    app.scale = pct
    core.apply_scale(app)
    core.upsert_app(app)
    return True
