"""
Keep a program's windows on one screen (multi-monitor setups).

Wine/Proton draws its windows through X11 (XWayland on Wayland desktops), so
we talk to the X server directly with ctypes - no extra packages needed:

* RandR tells us the screens ("monitors"): name (DP-1, HDMI-1 ...), position
  and size in X coordinates.
* The window manager lists the windows it manages in _NET_CLIENT_LIST on the
  root window. Each Fröccs program has its own window class
  ("steam_app_<game id>", see core.game_id), so we know which are ours.
* We ask the window manager to move a window with the standard EWMH message
  _NET_MOVERESIZE_WINDOW. Fullscreen windows get _NET_WM_FULLSCREEN_MONITORS
  ("be fullscreen on that screen"). GNOME, KDE, Xfce, Cinnamon... support both.

A small watcher process (`froccs keep-on-screen <id>`) starts with the program,
moves new windows to the chosen screen and brings back any that leave it,
and exits a little after the program closes.

The same watcher fixes "fullscreen that isn't": many games (World of Warcraft,
most "borderless"/"windowed fullscreen" modes) don't ask for fullscreen, they
just open a frameless window as big as the screen. The window manager treats
that as an ordinary window and keeps it out from under the top bar, so the
game ends up exactly one bar shorter than the screen. When we see a frameless
window that spans a whole screen except for (at most) a bar's worth, we ask
the window manager to make it properly fullscreen on that screen. Wine then
tells the game its window got bigger, and the game fills the screen.

Programs in native Wayland mode (Advanced → Wayland) can't be moved this way:
Wayland doesn't let one program position another's windows.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import fcntl
import os
import time
from dataclasses import dataclass
from typing import Optional

from .i18n import _
from .paths import DATA_DIR, DB_FILE

RUN_DIR = DATA_DIR / "run"


# ------------------------------------------------------------------ Xlib --

class _XRRMonitorInfo(ctypes.Structure):
    _fields_ = [("name", ctypes.c_ulong), ("primary", ctypes.c_int),
                ("automatic", ctypes.c_int), ("noutput", ctypes.c_int),
                ("x", ctypes.c_int), ("y", ctypes.c_int),
                ("width", ctypes.c_int), ("height", ctypes.c_int),
                ("mwidth", ctypes.c_int), ("mheight", ctypes.c_int),
                ("outputs", ctypes.POINTER(ctypes.c_ulong))]


class _XineramaScreenInfo(ctypes.Structure):
    _fields_ = [("screen_number", ctypes.c_int), ("x_org", ctypes.c_short),
                ("y_org", ctypes.c_short), ("width", ctypes.c_short),
                ("height", ctypes.c_short)]


class _XClientMessageEvent(ctypes.Structure):
    _fields_ = [("type", ctypes.c_int), ("serial", ctypes.c_ulong),
                ("send_event", ctypes.c_int), ("display", ctypes.c_void_p),
                ("window", ctypes.c_ulong), ("message_type", ctypes.c_ulong),
                ("format", ctypes.c_int), ("l", ctypes.c_long * 5)]


class _XEvent(ctypes.Union):
    _fields_ = [("xclient", _XClientMessageEvent), ("pad", ctypes.c_long * 24)]


class _XClassHint(ctypes.Structure):
    _fields_ = [("res_name", ctypes.c_void_p), ("res_class", ctypes.c_void_p)]


_ERROR_HANDLER = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)
_ignore_errors = _ERROR_HANDLER(lambda _d, _e: 0)   # windows vanish all the time

CLIENT_MESSAGE = 33
SUBSTRUCTURE_MASKS = (1 << 19) | (1 << 20)          # redirect + notify
XA_ATOM, XA_WINDOW = 4, 33
ANY_PROPERTY_TYPE = 0


def _lib(name: str):
    path = ctypes.util.find_library(name)
    for cand in filter(None, (path, f"lib{name}.so")):
        try:
            return ctypes.CDLL(cand)
        except OSError:
            pass
    return None


@dataclass
class Monitor:
    name: str          # RandR name, usually the connector: DP-1, HDMI-A-1, eDP-1
    x: int
    y: int
    width: int
    height: int
    primary: bool
    index: int         # Xinerama index, for _NET_WM_FULLSCREEN_MONITORS

    def contains(self, px: int, py: int) -> bool:
        return self.x <= px < self.x + self.width and self.y <= py < self.y + self.height


class X11:
    """Just the few Xlib calls we need."""

    def __init__(self, display: Optional[str] = None):
        self.x = _lib("X11")
        if not self.x:
            raise OSError("libX11 not found")
        x = self.x
        x.XOpenDisplay.restype = ctypes.c_void_p
        x.XOpenDisplay.argtypes = [ctypes.c_char_p]
        x.XDefaultRootWindow.restype = ctypes.c_ulong
        x.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        x.XInternAtom.restype = ctypes.c_ulong
        x.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
        x.XGetAtomName.restype = ctypes.c_void_p
        x.XGetAtomName.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        x.XFree.argtypes = [ctypes.c_void_p]
        x.XGetWindowProperty.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_long, ctypes.c_long,
            ctypes.c_int, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong),
            ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_ulong),
            ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_void_p)]
        x.XGetClassHint.argtypes = [ctypes.c_void_p, ctypes.c_ulong,
                                    ctypes.POINTER(_XClassHint)]
        x.XGetGeometry.argtypes = [ctypes.c_void_p, ctypes.c_ulong,
                                   ctypes.POINTER(ctypes.c_ulong),
                                   ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int),
                                   ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_uint),
                                   ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_uint)]
        x.XTranslateCoordinates.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_int, ctypes.c_int,
            ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_ulong)]
        x.XSendEvent.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int,
                                 ctypes.c_long, ctypes.POINTER(_XEvent)]
        x.XFlush.argtypes = [ctypes.c_void_p]
        x.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
        x.XCloseDisplay.argtypes = [ctypes.c_void_p]
        x.XSetErrorHandler(_ignore_errors)

        self.dpy = x.XOpenDisplay(display.encode() if display else None)
        if not self.dpy:
            raise OSError("cannot open the X display (is DISPLAY set?)")
        self.root = x.XDefaultRootWindow(self.dpy)
        self._atoms: dict[str, int] = {}

    def close(self) -> None:
        if self.dpy:
            self.x.XCloseDisplay(self.dpy)
            self.dpy = None

    def atom(self, name: str) -> int:
        if name not in self._atoms:
            self._atoms[name] = self.x.XInternAtom(self.dpy, name.encode(), False)
        return self._atoms[name]

    def atom_name(self, atom: int) -> str:
        p = self.x.XGetAtomName(self.dpy, atom)
        if not p:
            return ""
        try:
            return ctypes.string_at(p).decode(errors="replace")
        finally:
            self.x.XFree(p)

    def _prop32(self, win: int, name: str, rtype: int) -> list[int]:
        actual, fmt = ctypes.c_ulong(), ctypes.c_int()
        n, after, data = ctypes.c_ulong(), ctypes.c_ulong(), ctypes.c_void_p()
        ok = self.x.XGetWindowProperty(self.dpy, win, self.atom(name), 0, 4096, False, rtype,
                                       ctypes.byref(actual), ctypes.byref(fmt), ctypes.byref(n),
                                       ctypes.byref(after), ctypes.byref(data))
        if ok != 0 or not data.value:
            return []
        try:
            if fmt.value != 32:
                return []
            # format-32 properties come back as C longs
            return list(ctypes.cast(data, ctypes.POINTER(ctypes.c_ulong))[:n.value])
        finally:
            self.x.XFree(data)

    # ---- screens
    def monitors(self) -> list[Monitor]:
        xr = _lib("Xrandr")
        if xr:
            xr.XRRGetMonitors.restype = ctypes.POINTER(_XRRMonitorInfo)
            xr.XRRGetMonitors.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int,
                                          ctypes.POINTER(ctypes.c_int)]
            xr.XRRFreeMonitors.argtypes = [ctypes.POINTER(_XRRMonitorInfo)]
            n = ctypes.c_int()
            mons = xr.XRRGetMonitors(self.dpy, self.root, True, ctypes.byref(n))
            if mons:
                try:
                    return [Monitor(self.atom_name(m.name) or f"screen-{i + 1}", m.x, m.y,
                                    m.width, m.height, bool(m.primary), i)
                            for i, m in enumerate(mons[:n.value])]
                finally:
                    xr.XRRFreeMonitors(mons)
        xin = _lib("Xinerama")                      # very old setups: no names
        if xin:
            xin.XineramaQueryScreens.restype = ctypes.POINTER(_XineramaScreenInfo)
            xin.XineramaQueryScreens.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)]
            n = ctypes.c_int()
            scr = xin.XineramaQueryScreens(self.dpy, ctypes.byref(n))
            if scr:
                try:
                    return [Monitor(f"screen-{i + 1}", s.x_org, s.y_org, s.width, s.height,
                                    i == 0, i) for i, s in enumerate(scr[:n.value])]
                finally:
                    self.x.XFree(scr)
        return []

    # ---- windows
    def client_windows(self) -> list[int]:
        return self._prop32(self.root, "_NET_CLIENT_LIST", XA_WINDOW)

    def window_class(self, win: int) -> tuple[str, str]:
        hint = _XClassHint()
        if not self.x.XGetClassHint(self.dpy, win, ctypes.byref(hint)):
            return "", ""
        out = tuple(ctypes.string_at(p).decode(errors="replace") if p else ""
                    for p in (hint.res_name, hint.res_class))
        for p in (hint.res_name, hint.res_class):
            if p:
                self.x.XFree(p)
        return out

    def geometry(self, win: int) -> Optional[tuple[int, int, int, int]]:
        """(x, y, width, height) of the window's contents in screen coordinates."""
        root, wx, wy = ctypes.c_ulong(), ctypes.c_int(), ctypes.c_int()
        w, h, border, depth = ctypes.c_uint(), ctypes.c_uint(), ctypes.c_uint(), ctypes.c_uint()
        if not self.x.XGetGeometry(self.dpy, win, ctypes.byref(root), ctypes.byref(wx),
                                   ctypes.byref(wy), ctypes.byref(w), ctypes.byref(h),
                                   ctypes.byref(border), ctypes.byref(depth)):
            return None
        rx, ry, child = ctypes.c_int(), ctypes.c_int(), ctypes.c_ulong()
        if not self.x.XTranslateCoordinates(self.dpy, win, self.root, 0, 0, ctypes.byref(rx),
                                            ctypes.byref(ry), ctypes.byref(child)):
            return None
        return rx.value, ry.value, w.value, h.value

    def states(self, win: int) -> set[str]:
        return {self.atom_name(a) for a in self._prop32(win, "_NET_WM_STATE", XA_ATOM)}

    def frameless(self, win: int) -> bool:
        """No title bar or border drawn by the window manager."""
        extents = self._prop32(win, "_NET_FRAME_EXTENTS", ANY_PROPERTY_TYPE)
        if any(extents):
            return False
        # Motif hints: [flags, functions, decorations, ...]; flag 2 = decorations set
        hints = self._prop32(win, "_MOTIF_WM_HINTS", ANY_PROPERTY_TYPE)
        if len(hints) >= 3 and hints[0] & 2:
            return hints[2] == 0
        return bool(extents)        # WM said "0 border" explicitly

    def _message(self, win: int, name: str, data: list[int]) -> None:
        ev = _XEvent()
        ev.xclient.type = CLIENT_MESSAGE
        ev.xclient.send_event = True
        ev.xclient.window = win
        ev.xclient.message_type = self.atom(name)
        ev.xclient.format = 32
        for i, v in enumerate(data[:5]):
            ev.xclient.l[i] = v
        self.x.XSendEvent(self.dpy, self.root, False, SUBSTRUCTURE_MASKS, ctypes.byref(ev))
        self.x.XFlush(self.dpy)

    def move(self, win: int, x: int, y: int) -> None:
        # gravity NorthWest (1) | x and y given | source = pager (2): the window
        # manager treats it as a user request, not something the app asked for
        self._message(win, "_NET_MOVERESIZE_WINDOW",
                      [1 | (1 << 8) | (1 << 9) | (2 << 12), x, y, 0, 0])

    def set_state(self, win: int, add: bool, *states: str) -> None:
        atoms = [self.atom(s) for s in states] + [0]
        self._message(win, "_NET_WM_STATE", [1 if add else 0, atoms[0], atoms[1], 2, 0])

    def fullscreen_on(self, win: int, index: int) -> None:
        self._message(win, "_NET_WM_FULLSCREEN_MONITORS", [index, index, index, index, 2])

    def sync(self) -> None:
        self.x.XSync(self.dpy, False)


# --------------------------------------------------------------- helpers --

def monitors() -> list[Monitor]:
    """The screens as X11 programs see them; [] if X isn't reachable."""
    try:
        x = X11()
    except OSError:
        return []
    try:
        return x.monitors()
    finally:
        x.close()


def describe(m: Monitor, all_monitors: list[Monitor], gdk_names: dict[str, str]) -> str:
    """'DP-2 · Dell U2723QE · 3840×2160, right' for the settings list."""
    parts = [m.name]
    if gdk_names.get(m.name):
        parts.append(gdk_names[m.name])
    where = ""
    if len(all_monitors) > 1:
        xs = sorted({mm.x for mm in all_monitors})
        if len(xs) > 1:
            where = "left" if m.x == xs[0] else "right" if m.x == xs[-1] else "middle"
        else:
            ys = sorted({mm.y for mm in all_monitors})
            where = "top" if m.y == ys[0] else "bottom" if m.y == ys[-1] else "middle"
    size = f"{m.width}×{m.height}"
    parts.append(f"{size}, {_(where)}" if where else size)
    if m.primary:
        parts.append(_("main screen"))
    return " · ".join(parts)


def classes_for(app) -> set[str]:
    """Window classes of the program and its extra menu entries (lowercase)."""
    from . import core
    return {core.window_class(app, i).lower() for i in range(-1, len(app.shortcuts))}


def fixes_fullscreen(app) -> bool:
    # Native Wayland windows can't be touched; Gamescope is fullscreen anyway.
    return bool(getattr(app, "fullscreen_fix", False) and not getattr(app, "wayland", False)
                and not (getattr(app, "gamescope", False) and _have("gamescope")))


def _have(cmd: str) -> bool:
    import shutil
    return bool(shutil.which(cmd))


def rules_for(app) -> dict[str, str]:
    """{window class: screen name} for the program and its extra menu entries."""
    from . import core
    out = {}
    if getattr(app, "wayland", False):
        return out                        # native Wayland windows can't be moved
    if getattr(app, "gamescope", False) and _have("gamescope"):
        return out                        # Gamescope's window isn't the program's
    if app.screen:
        out[core.window_class(app, -1).lower()] = app.screen
    for i, s in enumerate(app.shortcuts):
        if s.get("screen"):
            out[core.window_class(app, i).lower()] = s["screen"]
    return out


def start_watcher(app) -> None:
    """Called at every launch; the watcher itself makes sure only one runs."""
    if not (rules_for(app) or fixes_fullscreen(app)) or not os.environ.get("DISPLAY"):
        return
    import subprocess
    from . import core
    subprocess.Popen(["sh", "-c", f"exec {core.launcher_command()} keep-on-screen {app.id}"],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)


# --------------------------------------------------------------- watcher --

@dataclass
class _Seen:
    pos: tuple[int, int] = (-1, -1)
    tries: int = 0
    fs_tries: int = 0
    geo: tuple = ()               # last geometry, to know when it stood still
    still: int = 0                # polls in a row with the same geometry
    fix_tries: int = 0            # fullscreen-fix attempts for this window


def _monitor_of(geo, mons) -> Optional[Monitor]:
    cx, cy = geo[0] + geo[2] // 2, geo[1] + geo[3] // 2
    return next((m for m in mons if m.contains(cx, cy)), None)


def wants_fullscreen(geo, states: set[str], frameless: bool, mon: Monitor) -> bool:
    """
    A frameless window that is as wide as the screen and as tall as it except
    for a bar's worth (top bar, dock: up to 10 %) is a game in "windowed
    fullscreen" that the window manager squeezed below the top bar. Maximized
    windows are left alone: those are programs that want to stay below it.
    """
    if not frameless or states & {"_NET_WM_STATE_FULLSCREEN", "_NET_WM_STATE_MAXIMIZED_VERT",
                                  "_NET_WM_STATE_MAXIMIZED_HORZ", "_NET_WM_STATE_HIDDEN"}:
        return False
    x, y, w, h = geo
    slack = mon.height // 10
    if abs(x - mon.x) > 2 or abs(w - mon.width) > 2:
        return False
    if not (mon.y <= y <= mon.y + slack) or h < mon.height - slack:
        return False
    return (x, y, w, h) != (mon.x, mon.y, mon.width, mon.height)


def _make_fullscreen(x: X11, win: int, mon: Monitor) -> None:
    x.fullscreen_on(win, mon.index)
    x.set_state(win, True, "_NET_WM_STATE_FULLSCREEN")


def _target_position(geo, mon: Monitor) -> tuple[int, int]:
    _x, _y, w, h = geo
    x = mon.x + max(0, (mon.width - w) // 2)
    y = mon.y + max(0, (mon.height - h) // 2)
    return x, y


def _place(x: X11, win: int, geo, mon: Monitor, seen: _Seen, fix: bool = False,
           mons: tuple = ()) -> None:
    st = x.states(win)
    here = _monitor_of(geo, mons)
    if here and "_NET_WM_STATE_FULLSCREEN" not in st and x.frameless(win):
        covers = tuple(geo) == (here.x, here.y, here.width, here.height)
        if covers or (fix and wants_fullscreen(geo, st, True, here)):
            # A frameless screen-sized window: GNOME won't move it at all (and
            # would squeeze it below the top bar if it did). "Be fullscreen on
            # that screen" works.
            _make_fullscreen(x, win, mon)
            return
    if "_NET_WM_STATE_FULLSCREEN" in st:
        if seen.fs_tries < 2:
            # the clean way: "be fullscreen on that screen"
            x.fullscreen_on(win, mon.index)
            seen.fs_tries += 1
            return
        # window manager ignored it: leave fullscreen, move, go fullscreen again
        x.set_state(win, False, "_NET_WM_STATE_FULLSCREEN")
        x.sync()
        time.sleep(0.3)
        x.move(win, *_target_position(geo, mon))
        x.sync()
        time.sleep(0.3)
        x.set_state(win, True, "_NET_WM_STATE_FULLSCREEN")
        seen.fs_tries = 0
        return
    maxed = [s for s in ("_NET_WM_STATE_MAXIMIZED_VERT", "_NET_WM_STATE_MAXIMIZED_HORZ")
             if s in st]
    if maxed:                       # maximized windows won't move until unmaximized
        x.set_state(win, False, *maxed)
        x.sync()
        time.sleep(0.2)
    x.move(win, *_target_position(geo, mon))
    if maxed:
        x.sync()
        time.sleep(0.2)
        x.set_state(win, True, *maxed)


def _load_rules(app_id: str) -> tuple[dict[str, str], object]:
    from . import core
    app = core.get_app(app_id)
    return (rules_for(app) if app else {}), app


def watch(app_id: str) -> int:
    """
    Runs while the program runs. Every half second: find our windows, and
    move any that aren't on their screen. A window that left its screen is
    only moved back once it has stood still for a moment, so we don't fight
    the user in the middle of a drag.
    """
    from . import core
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    lock = open(RUN_DIR / f"screen-{app_id}.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return 0                                    # another watcher has it

    rules, app = _load_rules(app_id)
    if app is None or not (rules or fixes_fullscreen(app)):
        return 0
    try:
        x = X11()
    except OSError:
        return 1
    prefix = app.prefix
    classes, fix = classes_for(app), fixes_fullscreen(app)
    db_mtime = DB_FILE.stat().st_mtime if DB_FILE.exists() else 0
    started = last_alive = last_check = time.monotonic()
    seen: dict[int, _Seen] = {}
    try:
        while True:
            now = time.monotonic()
            if now - last_check >= 3:
                last_check = now
                if core.prefix_pids(prefix):
                    last_alive = now
                elif now - last_alive > 15 and now - started > 60:
                    return 0                        # the program closed
                m = DB_FILE.stat().st_mtime if DB_FILE.exists() else 0
                if m != db_mtime:                   # settings changed meanwhile
                    db_mtime = m
                    rules, app = _load_rules(app_id)
                    if app is None:
                        return 0
                    classes, fix = classes_for(app), fixes_fullscreen(app)
                    if not (rules or fix):
                        return 0
            mon_list = x.monitors()
            mons = {m.name: m for m in mon_list}
            alive = set()
            for win in x.client_windows():
                name, cls = x.window_class(win)
                if cls.lower() not in classes and name.lower() not in classes:
                    continue                        # not one of ours
                st = x.states(win)
                if "_NET_WM_STATE_HIDDEN" in st:
                    continue                        # minimized
                geo = x.geometry(win)
                if not geo:
                    continue
                alive.add(win)
                s = seen.get(win)
                new = s is None
                if new:
                    s = seen[win] = _Seen(pos=(geo[0], geo[1]))
                s.still = s.still + 1 if s.geo == geo else 0
                s.geo = geo
                target = rules.get(cls.lower()) or rules.get(name.lower())
                mon = mons.get(target) if target else None
                cx, cy = geo[0] + geo[2] // 2, geo[1] + geo[3] // 2
                if mon and not mon.contains(cx, cy):
                    if new:
                        _place(x, win, geo, mon, s, fix, mon_list)   # brand-new: right away
                        s.tries += 1
                    elif s.pos == (geo[0], geo[1]) and s.tries < 6:
                        _place(x, win, geo, mon, s, fix, mon_list)   # stood still off it
                        s.tries += 1
                    elif s.pos != (geo[0], geo[1]):
                        s.pos = (geo[0], geo[1])    # still moving (dragged?): wait
                        s.tries = 0
                    continue
                s.pos, s.tries, s.fs_tries = (geo[0], geo[1]), 0, 0
                # On its screen. A game squeezed below the top bar? Wait until
                # it has settled (games resize a few times while starting).
                if fix and s.still >= 2 and s.fix_tries < 3:
                    here = mon or _monitor_of(geo, mon_list)
                    if here and wants_fullscreen(geo, st, x.frameless(win), here):
                        _make_fullscreen(x, win, here)
                        s.fix_tries += 1
                        s.still = 0
            for gone in set(seen) - alive:
                del seen[gone]
            time.sleep(0.5)
    finally:
        x.close()
