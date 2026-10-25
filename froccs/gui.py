"""
Fröccs window (GTK4 + libadwaita, the native GNOME look on Fedora).

Rule of thumb for GTK: never do slow work on the main thread or the window
freezes. Slow jobs run in a worker thread (run_bg) and report back with
GLib.idle_add, which runs a function on the main thread.
"""
from __future__ import annotations

import os
import shutil
import threading
import time
import urllib.parse
from pathlib import Path

import gi

from .i18n import apply_toolkit_language

apply_toolkit_language()          # must happen before GTK is loaded

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
gi.require_version("Pango", "1.0")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk, Pango  # noqa: E402

from . import (__version__, analyze, backup, core, devices, distro, fonts, health,  # noqa: E402
               integration, maint, proton, recipes, screens, updater)
from .i18n import _  # noqa: E402
from .paths import APP_ID, EXPORT_SUFFIX, LOG_DIR, drive_c  # noqa: E402

GRAPHICS = [("dxvk", "Vulkan (DXVK) - fastest, for 3D/CAD"),
            ("wined3d", "OpenGL (WineD3D) - most compatible for 2D tools")]
DLL_MODE_KEYS = list(core.DLL_MODES)
PREP_WHY = ("Why: Fröccs builds two ready-made Windows environments once, one basic and "
            "one with .NET 4.8. Every program you add starts from an instant copy of one of "
            "them, so you don't wait about 15 minutes for .NET in the middle of an install. "
            "You can keep using your programs meanwhile.")


# ---------------------------------------------------------------- helpers --

def run_bg(fn, on_done=None, on_error=None):
    """Run fn() in a thread; call on_done(result) / on_error(msg) on the main thread."""
    def work():
        try:
            result = fn()
        except Exception as e:                       # show, don't crash
            if on_error:
                GLib.idle_add(on_error, str(e))
            return
        if on_done:
            GLib.idle_add(on_done, result)
    threading.Thread(target=work, daemon=True).start()


def spinner(size: int = 0) -> Gtk.Widget:
    """Adw.Spinner needs libadwaita 1.6; older systems get GTK's spinner."""
    if hasattr(Adw, "Spinner"):
        w = Adw.Spinner()
    else:
        w = Gtk.Spinner(spinning=True)
    if size:
        w.set_size_request(size, size)
    return w


def row(title: str, subtitle: str = "", **kw) -> Adw.ActionRow:
    """ActionRow that shows text literally (paths and names may contain & or <)."""
    r = Adw.ActionRow(title=title, subtitle=subtitle, use_markup=False, **kw)
    return r


def file_filter(name: str, patterns: list[str]) -> Gio.ListStore:
    f = Gtk.FileFilter()
    f.set_name(name)
    for p in patterns:
        f.add_pattern(p)
        f.add_pattern(p.upper())
    store = Gio.ListStore.new(Gtk.FileFilter)
    store.append(f)
    return store


def pick_file(parent, title, patterns, folder: Path | None, callback):
    dlg = Gtk.FileDialog(title=title)
    if patterns:
        dlg.set_filters(file_filter(title, patterns))
    if folder and folder.is_dir():
        dlg.set_initial_folder(Gio.File.new_for_path(str(folder)))

    def done(d, res):
        try:
            f = d.open_finish(res)
        except GLib.Error:
            return
        callback(Path(f.get_path()))
    dlg.open(parent, None, done)


def pick_folder(parent, title, callback, folder: Path | None = None):
    dlg = Gtk.FileDialog(title=title)
    if folder and folder.is_dir():
        dlg.set_initial_folder(Gio.File.new_for_path(str(folder)))

    def done(d, res):
        try:
            f = d.select_folder_finish(res)
        except GLib.Error:
            return
        callback(Path(f.get_path()))
    dlg.select_folder(parent, None, done)


def downloads_dir() -> Path | None:
    # The name depends on the language (~/Letöltések on a Hungarian system)
    d = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOWNLOAD)
    return Path(d) if d and Path(d).is_dir() else None


def screen_scale(widget: Gtk.Widget) -> int | None:
    """The desktop's own scaling in percent (125 on a typical laptop), if known."""
    try:
        display = widget.get_display()
        native = widget.get_native()
        surface = native.get_surface() if native else None
        mon = display.get_monitor_at_surface(surface) if surface else None
        if mon is None:
            mon = display.get_monitors().get_item(0)
        scale = mon.get_scale() if hasattr(mon, "get_scale") else mon.get_scale_factor()
        return round(scale * 100)
    except Exception:
        return None


def scale_row(value: int) -> Adw.SpinRow:
    r = Adw.SpinRow.new_with_range(100, 300, 5)
    r.set_title(_("Display scaling"))
    r.set_subtitle(_("100 % = normal. Use 125 % or 150 % on laptop screens."))
    r.set_value(value)
    return r


def warning_row(text: str) -> Adw.ActionRow:
    r = row(_("Heads-up"), text, subtitle_lines=0)
    r.add_prefix(Gtk.Image(icon_name="dialog-warning-symbolic"))
    return r


def string_list(items: list[str]) -> Gtk.StringList:
    return Gtk.StringList.new(items)


def open_path(path: Path):
    Gio.AppInfo.launch_default_for_uri(Gio.File.new_for_path(str(path)).get_uri(), None)


def copy_text(widget: Gtk.Widget, text: str):
    widget.get_clipboard().set_content(Gdk.ContentProvider.new_for_value(text))


def env_to_text(env: dict) -> str:
    return " ".join(f"{k}={v}" for k, v in env.items())


def text_to_env(text: str) -> dict:
    out = {}
    for part in text.split():
        if "=" in part:
            k, v = part.split("=", 1)
            if k:
                out[k] = v
    return out


def app_icon(app: core.App, size: int) -> Gtk.Image:
    if app.icon and Path(app.icon).exists():
        img = Gtk.Image.new_from_file(app.icon)
    else:
        img = Gtk.Image.new_from_icon_name("application-x-executable")
    img.set_pixel_size(size)
    return img


# --------------------------------------------------------------- add flow --

class AddDialog(Adw.Dialog):
    """Options -> progress -> choose programs -> done.
    With `imported=(app, candidates)` it starts at the last step."""

    def __init__(self, window: "MainWindow", installer: Path | None = None, imported=None):
        super().__init__(title=_("Add software"), content_width=500, content_height=620)
        self.window, self.installer = window, installer
        self.app: core.App | None = None
        self.recipe: recipes.Recipe | None = None
        self.analysis: analyze.Analysis | None = None
        self.stop = threading.Event()
        self.finished = False

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.SLIDE_LEFT)
        if installer:
            self.stack.add_named(self._options_page(), "options")
        self.stack.add_named(self._progress_page(), "progress")
        self.stack.add_named(self._choose_page(), "choose")

        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        view.set_content(self.stack)
        self.set_child(view)
        self.connect("closed", self._on_closed)

        if imported:
            self.app, cands = imported
            self.set_title(_("Import"))
            self._on_installed(cands, [])
        else:
            run_bg(lambda: analyze.analyze(installer), self._on_analyzed)

    # -- page 1: options
    def _options_page(self) -> Gtk.Widget:
        page = Adw.PreferencesPage()
        g = Adw.PreferencesGroup(description=_("Installer: {name}").format(name=self.installer.name))
        page.add(g)
        self.name_row = Adw.EntryRow(title=_("Name in the apps menu"))
        self.name_row.set_text(core.guess_name(self.installer))
        g.add(self.name_row)
        self.scale = scale_row(core.load_settings().get("default_scale", 100))
        detected = screen_scale(self.window)
        if detected and detected != 100:
            self.scale.set_subtitle(_("Your screen runs at {pct} %. 100 % = normal size.")
                                    .format(pct=detected))
        g.add(self.scale)

        self.info_group = Adw.PreferencesGroup()
        self.checking = row(_("Checking the installer…"))
        self.checking.add_prefix(spinner())
        self.info_group.add(self.checking)
        page.add(self.info_group)

        adv = Adw.PreferencesGroup()
        exp = Adw.ExpanderRow(title=_("Advanced"),
                              subtitle=_("Set automatically. Change only if needed."))
        self.dotnet_row = Adw.SwitchRow(
            title=_(".NET Framework 4.8"),
            subtitle=_("Needed by many engineering and business programs."))
        self.gfx_row = Adw.ComboRow(title=_("Graphics"),
                                    model=string_list([_(t) for _k, t in GRAPHICS]))
        exp.add_row(self.dotnet_row)
        exp.add_row(self.gfx_row)
        adv.add(exp)
        page.add(adv)

        btn = Gtk.Button(label=_("Install"), halign=Gtk.Align.CENTER,
                         css_classes=["suggested-action", "pill"], margin_top=12)
        btn.connect("clicked", self._start)
        g2 = Adw.PreferencesGroup()
        g2.add(btn)
        page.add(g2)
        return page

    def _on_analyzed(self, a: analyze.Analysis):
        self.analysis = a
        self.recipe = recipes.find(self.installer.name, self.name_row.get_text())
        self.info_group.remove(self.checking)
        if self.recipe:
            r = row(_("Recognized: {name}").format(name=self.recipe.name),
                    _(self.recipe.notes) if self.recipe.notes else
                    _("Known-good settings are applied automatically."), subtitle_lines=0)
            r.add_prefix(Gtk.Image(icon_name="emblem-ok-symbolic"))
            self.info_group.add(r)
            if self.recipe.warning:
                self.info_group.add(warning_row(_(self.recipe.warning)))
        name = self.recipe.name if self.recipe else self.name_row.get_text()
        compat = row(_("Experiences of other users"),
                     _("See how well {name} runs for others before installing.").format(name=name))
        for label, url in (("WineHQ AppDB", "https://appdb.winehq.org/objectManager.php?"
                            "sClass=application&sTitle=Browse+Applications&"
                            "iappFamily-appNameOp=2&sappFamily-appNameData="),
                           ("ProtonDB", "https://www.protondb.com/search?q=")):
            b = Gtk.Button(label=label, valign=Gtk.Align.CENTER, css_classes=["flat"])
            b.connect("clicked", lambda _b, u=url: Gtk.UriLauncher.new(
                u + urllib.parse.quote(name)).launch(self.window, None, None, None))
            compat.add_suffix(b)
        self.info_group.add(compat)
        what = a.kind
        fw = ", ".join(a.frameworks) if a.frameworks else _("nothing special detected")
        self.info_group.add(row(_(what), _("Uses: {list}").format(list=fw)))
        for w in a.warnings:
            self.info_group.add(warning_row(_(w)))
        dotnet = self.recipe.dotnet if self.recipe else a.dotnet
        self.dotnet_row.set_active(dotnet)
        gfx = self.recipe.graphics if self.recipe else "dxvk"
        self.gfx_row.set_selected([k for k, _t in GRAPHICS].index(gfx))
        if a.dotnet_core:
            self.info_group.add(warning_row(_(
                "This program uses modern .NET. Most installers bring it along; if the "
                "program says it's missing, install the .NET Desktop Runtime from "
                "Microsoft with “Run another .exe here”.")))

    # -- page 2: progress
    def _progress_page(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18,
                      valign=Gtk.Align.CENTER, margin_start=32, margin_end=32)
        self.spinner = spinner(48)
        self.status = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER,
                                css_classes=["title-4"])
        self.bar = Gtk.ProgressBar()
        self.done_btn = Gtk.Button(label=_("The installer is finished"),
                                   halign=Gtk.Align.CENTER, visible=False, css_classes=["pill"])
        self.done_btn.set_tooltip_text(_(
            "Some installers leave background services running, so Fröccs can't tell "
            "when they are done. Click this once the installer window is closed."))
        self.done_btn.connect("clicked", lambda *_a: self.stop.set())
        for w in (self.spinner, self.status, self.bar, self.done_btn):
            box.append(w)
        return box

    # -- page 3: choose programs
    def _choose_page(self) -> Gtk.Widget:
        page = Adw.PreferencesPage()
        self.choose_group = Adw.PreferencesGroup(
            title=_("Which programs go in your apps menu?"),
            description=_("The first ticked one is the main program."))
        page.add(self.choose_group)
        self.warn_group = Adw.PreferencesGroup()
        page.add(self.warn_group)
        browse = Gtk.Button(label=_("Browse…"), css_classes=["flat"])
        browse.connect("clicked", self._browse)
        self.choose_group.set_header_suffix(browse)
        finish = Gtk.Button(label=_("Add to apps menu"), halign=Gtk.Align.CENTER,
                            css_classes=["suggested-action", "pill"], margin_top=12)
        finish.connect("clicked", self._finish)
        g = Adw.PreferencesGroup()
        g.add(finish)
        page.add(g)
        self.choices: list[tuple[Gtk.CheckButton, Path]] = []
        return page

    # -- actions
    def _start(self, *_a):
        name = self.name_row.get_text().strip() or core.guess_name(self.installer)
        r = self.recipe
        self.app = core.App(
            id=core.make_id(name), name=name, scale=int(self.scale.get_value()),
            dotnet=self.dotnet_row.get_active(),
            graphics=GRAPHICS[self.gfx_row.get_selected()][0],
            dll_overrides=dict(r.dll_overrides) if r else {},
            env=dict(r.env) if r else {},
            file_types=[{"ext": e, "shortcut": -1} for e in (r.file_types if r else [])],
            recipe=r.id if r else "")
        verbs = tuple(r.verbs) if r else ()
        edits = tuple(tuple(e) for e in r.registry) if r else ()
        self.set_can_close(False)
        self.stack.set_visible_child_name("progress")

        def progress(text, frac):
            GLib.idle_add(self._on_progress, text, frac)

        def work():
            return core.install(self.app, self.installer, progress, self.stop, verbs, edits)

        def failed(msg):
            core.discard(self.app)
            self._on_error(msg)
        run_bg(work, lambda res: self._on_installed(*res), failed)

    def _on_progress(self, text, frac):
        self.status.set_text(text)
        self.bar.set_fraction(frac)
        self.done_btn.set_visible(text == core.running_installer_text())

    def _on_error(self, msg):
        self.set_can_close(True)
        self.close()
        d = Adw.AlertDialog(heading=_("Installation failed"), body=msg)
        d.add_response("ok", _("OK"))
        d.present(self.window)

    def _on_installed(self, cands: list[Path], failed: list[str]):
        self.set_can_close(True)
        for i, path in enumerate(cands):
            self._add_choice(path, active=(i == 0))
        if not cands:
            self.choose_group.set_description(
                _("No new program was found. Use “Browse…” to pick it yourself."))
        if failed:
            names = ", ".join(_(core.VERB_NAMES.get(v, v)) for v in failed)
            self.warn_group.add(warning_row(
                _("Some components failed to install: {names}. The program may still work.")
                .format(names=names)))
        self.stack.set_visible_child_name("choose")
        if self.installer:
            self.window.notify_done(_("{name} is installed").format(name=self.app.name),
                                    _("Choose which programs go in your apps menu."))

    def _add_choice(self, path: Path, active: bool = False):
        check = Gtk.CheckButton(valign=Gtk.Align.CENTER, active=active)
        root = drive_c(self.app.prefix)
        try:
            rel = "C:\\" + str(path.relative_to(root).parent).replace("/", "\\")
        except ValueError:
            rel = str(path.parent)
        r = row(path.name, rel, activatable_widget=check)
        r.add_prefix(check)
        self.choose_group.add(r)
        self.choices.append((check, path))

    def _browse(self, *_a):
        pick_file(self.window, _("Choose the program"), ["*.exe"],
                  drive_c(self.app.prefix), lambda p: self._add_choice(p, active=True))

    def _finish(self, *_a):
        exes = [p for c, p in self.choices if c.get_active()]
        if not exes:
            self._browse()
            return
        integration.finalize(self.app, exes)
        self.finished = True
        self.window.refresh()
        self.window.toast(_("{name} is now in your apps menu").format(name=self.app.name))
        self.close()

    def _on_closed(self, *_a):
        if self.app and not self.finished and not core.get_app(self.app.id):
            run_bg(lambda: core.discard(self.app))    # clean up a half-made app


# --------------------------------------------------------------- settings --

class SettingsDialog(Adw.PreferencesDialog):
    """Per-app settings. Changes are saved when the dialog closes."""

    def __init__(self, window: "MainWindow", app: core.App):
        super().__init__(title=app.name, search_enabled=False)
        self.window, self.app = window, app
        self.restored = False
        self.add(self._general_page())
        self.add(self._files_page())
        self.add(self._advanced_page())
        self.add(self._backup_page())
        self.connect("closed", self._save)

    # ---- General
    def _general_page(self):
        page = Adw.PreferencesPage(title=_("General"), icon_name="preferences-system-symbolic")
        g = Adw.PreferencesGroup()
        self.name_row = Adw.EntryRow(title=_("Name"))
        self.name_row.set_text(self.app.name)
        g.add(self.name_row)
        self.scale = scale_row(self.app.scale)
        g.add(self.scale)
        now = screens.auto_percent(self.app)
        self.scale_auto = Adw.SwitchRow(
            title=_("Match my screen automatically"),
            subtitle=(_("At each start, use the scaling of the screen. Right now: {pct} %")
                      .format(pct=now) if now else
                      _("Not available: couldn't read the screen setup from your desktop.")))
        self.scale_auto.set_active(self.app.scale_auto and now is not None)
        self.scale_auto.set_sensitive(now is not None)
        self.scale.set_sensitive(not self.scale_auto.get_active())
        self.scale_auto.connect("notify::active",
                                lambda r, _p: self.scale.set_sensitive(not r.get_active()))
        g.add(self.scale_auto)
        self.autostart = Adw.SwitchRow(title=_("Start when I log in"),
                                       subtitle=_("Useful for licence servers and gateways."))
        self.autostart.set_active(self.app.autostart)
        g.add(self.autostart)
        self.tray = Adw.SwitchRow(
            title=_("Tray icon window"),
            subtitle=_("GNOME has no tray, so Wine shows the program's tray icons in a "
                       "small window of its own. Turn on if the program hides in the tray "
                       "and you need the icon to get it back."))
        self.tray.set_active(self.app.tray_window)
        g.add(self.tray)
        page.add(g)

        lo = Adw.PreferencesGroup(title=_("Launch options"))
        self.lo_on = Adw.SwitchRow(title=_("Use launch options"))
        self.lo_on.set_active(self.app.launch_options_on)
        self.args_row = Adw.EntryRow(title=_("Arguments"))
        self.args_row.set_text(self.app.args)
        self.env_row = Adw.EntryRow(title=_("Environment variables (NAME=value …)"))
        self.env_row.set_text(env_to_text(self.app.env))
        for w in (self.lo_on, self.args_row, self.env_row):
            lo.add(w)
        page.add(lo)

        self.sc_group = Adw.PreferencesGroup(
            title=_("Apps menu entries"),
            description=_("Extra programs from this app, e.g. configuration tools."))
        add = Gtk.Button(icon_name="list-add-symbolic", css_classes=["flat"],
                         tooltip_text=_("Add an entry"))
        add.connect("clicked", self._add_shortcut)
        self.sc_group.set_header_suffix(add)
        page.add(self.sc_group)
        self.sc_rows = []
        self._fill_shortcuts()
        return page

    def _fill_shortcuts(self):
        for r in self.sc_rows:
            self.sc_group.remove(r)
        self.sc_rows = []
        main = row(self.app.name, self.app.exe, subtitle_lines=1)
        main.add_suffix(Gtk.Label(label=_("main"), css_classes=["dim-label"]))
        change = Gtk.Button(icon_name="document-edit-symbolic", valign=Gtk.Align.CENTER,
                            css_classes=["flat"], tooltip_text=_("Change the main program"))
        change.connect("clicked", self._change_main)
        main.add_suffix(change)
        self.sc_group.add(main)
        self.sc_rows.append(main)
        for i, s in enumerate(self.app.shortcuts):
            r = row(s["name"], s["exe"], subtitle_lines=1)
            rm = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER,
                            css_classes=["flat"], tooltip_text=_("Remove"))
            rm.connect("clicked", lambda _b, i=i: self._remove_shortcut(i))
            r.add_suffix(rm)
            self.sc_group.add(r)
            self.sc_rows.append(r)

    def _change_main(self, *_a):
        def done(p):
            self.app.exe = str(p)
            self.app.icon = integration.extract_icon(p, self.app.id) or self.app.icon
            self._fill_shortcuts()
        pick_file(self.window, _("Choose the program"), ["*.exe"], drive_c(self.app.prefix), done)

    def _add_shortcut(self, *_a):
        def done(p):
            integration.add_shortcut(self.app, p)
            self._fill_shortcuts()
        pick_file(self.window, _("Choose the program"), ["*.exe"], drive_c(self.app.prefix), done)

    def _remove_shortcut(self, i):
        integration.remove_shortcut(self.app, i)
        self._fill_shortcuts()
        self._fill_types()

    # ---- Files
    def _files_page(self):
        page = Adw.PreferencesPage(title=_("Files"), icon_name="folder-symbolic")
        g = Adw.PreferencesGroup(title=_("Folders"))
        self.share = Adw.SwitchRow(
            title=_("Use my own folders"),
            subtitle=_("The program's Documents, Desktop, Downloads… are your real folders. "
                       "Off: the program gets private ones."))
        self.share.set_active(self.app.share_home)
        g.add(self.share)
        self.hide_z = Adw.SwitchRow(
            title=_("Hide the rest of my system"),
            subtitle=_("Removes drive Z: (your whole Linux system). Your home folder is "
                       "still reachable as H:."))
        self.hide_z.set_active(self.app.hide_z)
        g.add(self.hide_z)
        page.add(g)

        self.drv_group = Adw.PreferencesGroup(
            title=_("Drives"), description=_("Show a folder to the program as a drive letter."))
        add = Gtk.Button(icon_name="list-add-symbolic", css_classes=["flat"],
                         tooltip_text=_("Add a drive"))
        add.connect("clicked", self._add_drive)
        self.drv_group.set_header_suffix(add)
        page.add(self.drv_group)
        self.drv_rows = []
        self._fill_drives()

        self.ft_group = Adw.PreferencesGroup(
            title=_("File types"),
            description=_("These files get “Open with {name}” in your file manager.")
            .format(name=self.app.name))
        self.ft_entry = Adw.EntryRow(title=_("Add a file type, e.g. dwg"))
        self.ft_entry.set_show_apply_button(True)
        self.ft_entry.connect("apply", self._add_type)
        page.add(self.ft_group)
        self.ft_rows = []
        self._fill_types()

        self.font_group = Adw.PreferencesGroup(
            title=_("Fonts"),
            description=_("Extra fonts for this program, e.g. ISO or company fonts used in "
                          "drawings. Close the program before changing them."))
        fadd = Gtk.Button(icon_name="list-add-symbolic", css_classes=["flat"],
                          tooltip_text=_("Install fonts…"))
        fadd.connect("clicked", self._add_fonts)
        self.font_group.set_header_suffix(fadd)
        page.add(self.font_group)
        self.font_rows = []
        self._fill_fonts()

        sp = Adw.PreferencesGroup(
            title=_("Serial ports"),
            description=_("Connect PLC cables (USB-serial adapters) to COM ports."))
        self.devs = devices.serial_devices()
        labels = [_("Not connected")] + [d for _p, d in self.devs]
        self.port_rows = {}
        for n in range(1, 5):
            name = f"COM{n}"
            cr = Adw.ComboRow(title=name, model=string_list(labels))
            cur = self.app.com_ports.get(name)
            paths = [p for p, _d in self.devs]
            if cur in paths:
                cr.set_selected(paths.index(cur) + 1)
            elif cur:
                labels.append(cur)
                cr.set_model(string_list(labels))
                cr.set_selected(len(labels) - 1)
            sp.add(cr)
            self.port_rows[name] = (cr, list(labels))
        if not self.devs:
            sp.set_description(sp.get_description() + "\n" +
                               _("No adapter is plugged in right now."))
        page.add(sp)
        return page

    def _fill_fonts(self):
        for r in self.font_rows:
            self.font_group.remove(r)
        self.font_rows = []
        for fname in self.app.fonts:
            r = row(fname)
            rm = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER,
                            css_classes=["flat"], tooltip_text=_("Remove"))
            rm.connect("clicked", lambda _b, f=fname: self._remove_font(f))
            r.add_suffix(rm)
            self.font_group.add(r)
            self.font_rows.append(r)

    def _add_fonts(self, *_a):
        dlg = Gtk.FileDialog(title=_("Install fonts…"))
        dlg.set_filters(file_filter(_("Fonts"), fonts.FONT_PATTERNS))

        def done(d, res):
            try:
                files = [Path(f.get_path()) for f in d.open_multiple_finish(res)]
            except GLib.Error:
                return
            try:
                fonts.install_fonts(self.app, files)
            except core.InstallError as e:
                self.add_toast(Adw.Toast(title=_(str(e))))
            self._fill_fonts()
        dlg.open_multiple(self.window, None, done)

    def _remove_font(self, fname):
        try:
            fonts.remove_font(self.app, fname)
        except core.InstallError as e:
            self.add_toast(Adw.Toast(title=_(str(e))))
        self._fill_fonts()

    def _fill_drives(self):
        for r in self.drv_rows:
            self.drv_group.remove(r)
        self.drv_rows = []
        for letter, path in sorted(self.app.drives.items()):
            r = row(f"{letter.upper()}:", path)
            rm = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER,
                            css_classes=["flat"], tooltip_text=_("Remove"))
            rm.connect("clicked", lambda _b, l=letter: self._remove_drive(l))
            r.add_suffix(rm)
            self.drv_group.add(r)
            self.drv_rows.append(r)

    def _add_drive(self, *_a):
        def done(p):
            self.app.drives[devices.free_letter(self.app)] = str(p)
            self._fill_drives()
        pick_folder(self.window, _("Choose a folder"), done)

    def _remove_drive(self, letter):
        self.app.drives.pop(letter, None)
        self._fill_drives()

    def _fill_types(self):
        for r in self.ft_rows:
            self.ft_group.remove(r)
        self.ft_rows = []
        names = [self.app.name] + [s["name"] for s in self.app.shortcuts]
        for ft in self.app.file_types:
            idx = ft.get("shortcut", -1)
            r = row(f".{ft['ext']}", names[idx + 1] if idx + 1 < len(names) else names[0])
            rm = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER,
                            css_classes=["flat"], tooltip_text=_("Remove"))
            rm.connect("clicked", lambda _b, e=ft["ext"]: self._remove_type(e))
            r.add_suffix(rm)
            self.ft_group.add(r)
            self.ft_rows.append(r)
        self.ft_group.add(self.ft_entry)
        self.ft_rows.append(self.ft_entry)

    def _add_type(self, entry):
        ext = entry.get_text().strip().lstrip("*.").lower()
        if ext and ext not in [f["ext"] for f in self.app.file_types]:
            self.app.file_types.append({"ext": ext, "shortcut": -1})
            if ext in self.app.file_types_removed:
                self.app.file_types_removed.remove(ext)
        entry.set_text("")
        self._fill_types()

    def _remove_type(self, ext):
        self.app.file_types = [f for f in self.app.file_types if f["ext"] != ext]
        self.app.file_types_removed.append(ext)
        self._fill_types()

    # ---- Advanced
    def _advanced_page(self):
        page = Adw.PreferencesPage(title=_("Advanced"), icon_name="applications-engineering-symbolic")
        g = Adw.PreferencesGroup(title=_("Compatibility"))
        self.builds = proton.installed()
        latest = proton.newest_ge()
        labels = [_("Newest (currently {name})").format(name=latest or "GE-Proton")] + self.builds
        self.proton_row = Adw.ComboRow(
            title=_("Proton version"),
            subtitle=_("Pin a version so updates can't break a working program."),
            model=string_list(labels))
        if self.app.proton in self.builds:
            self.proton_row.set_selected(self.builds.index(self.app.proton) + 1)
        g.add(self.proton_row)
        self.gfx = Adw.ComboRow(title=_("Graphics"),
                                model=string_list([_(t) for _k, t in GRAPHICS]))
        self.gfx.set_selected([k for k, _t in GRAPHICS].index(self.app.graphics)
                              if self.app.graphics in dict(GRAPHICS) else 0)
        g.add(self.gfx)
        self.ntsync = Adw.SwitchRow(
            title=_("NTSYNC"),
            subtitle=_("Faster for heavy, multi-threaded programs.") if core.ntsync_available()
            else _("Not available: see the health check."))
        self.ntsync.set_active(self.app.ntsync)
        self.ntsync.set_sensitive(core.ntsync_available())
        g.add(self.ntsync)
        self.wayland = Adw.SwitchRow(
            title=_("Native Wayland (experimental)"),
            subtitle=_("Sharper on scaled screens, but some programs break."))
        self.wayland.set_active(self.app.wayland)
        g.add(self.wayland)
        page.add(g)

        self.dll_group = Adw.PreferencesGroup(
            title=_("DLL overrides"),
            description=_("Choose whether Windows' own (native) or Wine's (built-in) "
                          "version of a DLL is used. Many program fixes are exactly this."))
        self.dll_entry = Adw.EntryRow(title=_("Add a DLL, e.g. msvcp140"))
        self.dll_entry.set_show_apply_button(True)
        self.dll_entry.connect("apply", self._add_dll)
        page.add(self.dll_group)
        self.dll_rows = []
        self._fill_dlls()

        gm = Adw.PreferencesGroup(title=_("Games"))
        self.gamemode = Adw.SwitchRow(
            title=_("GameMode"),
            subtitle=_("Switches the computer to full performance while the program runs.")
            if shutil.which("gamemoderun") else
            _("Not installed: {cmd}").format(cmd=distro.install_hint("gamemode")))
        self.gamemode.set_active(self.app.gamemode)
        self.gamemode.set_sensitive(bool(shutil.which("gamemoderun")))
        gm.add(self.gamemode)
        self.mangohud = Adw.SwitchRow(
            title=_("MangoHud overlay"),
            subtitle=_("Shows FPS, CPU and GPU load on top of the program.")
            if shutil.which("mangohud") else
            _("Not installed: {cmd}").format(cmd=distro.install_hint("mangohud")))
        self.mangohud.set_active(self.app.mangohud)
        self.mangohud.set_sensitive(bool(shutil.which("mangohud")))
        gm.add(self.mangohud)
        self.fps = Adw.SpinRow.new_with_range(0, 360, 5)
        self.fps.set_title(_("FPS limit"))
        self.fps.set_subtitle(_("0 = no limit. Saves battery and keeps the laptop cooler."))
        self.fps.set_value(self.app.fps_limit)
        gm.add(self.fps)
        page.add(gm)

        t = Adw.PreferencesGroup(title=_("Troubleshooting"))
        self.links_row = row(_("Login & web links"), self._links_text(self.app.protocols),
                             subtitle_lines=3)
        scan = Gtk.Button(label=_("Scan again"), valign=Gtk.Align.CENTER)
        scan.connect("clicked", self._scan)
        self.links_row.add_suffix(scan)
        t.add(self.links_row)
        self.debug = Adw.SwitchRow(title=_("Debug mode"),
                                   subtitle=_("Writes a detailed log of what Wine does. "
                                              "Slower; turn off when done."))
        self.debug.set_active(self.app.debug)
        t.add(self.debug)
        logs = row(_("Logs"), str(LOG_DIR))
        ob = Gtk.Button(label=_("Open"), valign=Gtk.Align.CENTER)
        ob.connect("clicked", lambda *_a: (LOG_DIR.mkdir(parents=True, exist_ok=True),
                                           open_path(LOG_DIR)))
        logs.add_suffix(ob)
        t.add(logs)
        page.add(t)
        return page

    def _fill_dlls(self):
        for r in self.dll_rows:
            self.dll_group.remove(r)
        self.dll_rows = []
        for dll, mode in sorted(self.app.dll_overrides.items()):
            cr = Adw.ComboRow(title=dll, use_markup=False,
                              model=string_list([_(m) for m in DLL_MODE_KEYS]))
            cr.set_selected(DLL_MODE_KEYS.index(mode) if mode in DLL_MODE_KEYS else 0)
            cr.connect("notify::selected", lambda r, _p, d=dll:
                       self.app.dll_overrides.__setitem__(d, DLL_MODE_KEYS[r.get_selected()]))
            rm = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER,
                            css_classes=["flat"], tooltip_text=_("Remove"))
            rm.connect("clicked", lambda _b, d=dll: self._remove_dll(d))
            cr.add_suffix(rm)
            self.dll_group.add(cr)
            self.dll_rows.append(cr)
        self.dll_group.add(self.dll_entry)
        self.dll_rows.append(self.dll_entry)

    def _add_dll(self, entry):
        dll = entry.get_text().strip().lower()
        if dll.endswith(".dll"):
            dll = dll[:-4]
        if dll:
            self.app.dll_overrides.setdefault(dll, "native,builtin")
        entry.set_text("")
        self._fill_dlls()

    def _remove_dll(self, dll):
        self.app.dll_overrides.pop(dll, None)
        self._fill_dlls()

    @staticmethod
    def _links_text(schemes):
        if not schemes:
            return _("None found. If the program logs in through your browser, start it "
                     "once, then click Scan again.")
        return _("Your browser sends these links to this program: {list}").format(
            list=", ".join(f"{s}:" for s in schemes))

    def _scan(self, *_a):
        integration.rescan(self.app)
        self.links_row.set_subtitle(self._links_text(self.app.protocols))
        self._fill_types()

    # ---- Backup
    def _backup_page(self):
        page = Adw.PreferencesPage(title=_("Backup"), icon_name="document-save-symbolic")
        self.snap_group = Adw.PreferencesGroup(
            title=_("Restore points"),
            description=_("A saved copy of the program and its settings. Make one before "
                          "updates or experiments; go back if something breaks."))
        make = Gtk.Button(label=_("Create"), css_classes=["flat"])
        make.connect("clicked", self._make_snapshot)
        self.snap_group.set_header_suffix(make)
        page.add(self.snap_group)
        self.snap_rows = []
        self._fill_snapshots()

        ex = Adw.PreferencesGroup(title=_("Move to another computer"))
        er = row(_("Export"), _("Save this program with all its settings as one file. "
                                "Import it in Fröccs on the other computer."))
        eb = Gtk.Button(label=_("Export…"), valign=Gtk.Align.CENTER)
        eb.connect("clicked", lambda *_a: self.window.export_app(self.app))
        er.add_suffix(eb)
        ex.add(er)
        page.add(ex)
        return page

    def _fill_snapshots(self):
        for r in self.snap_rows:
            self.snap_group.remove(r)
        self.snap_rows = []
        snaps = backup.list_snapshots(self.app)
        if not snaps:
            r = row(_("No restore points yet"))
            self.snap_group.add(r)
            self.snap_rows.append(r)
        for s in snaps:
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(s["time"]))
            r = row(_(s["label"]) if s.get("auto") else s["label"],
                    when + ("  ·  " + _("automatic") if s.get("auto") else ""))
            rb = Gtk.Button(label=_("Restore"), valign=Gtk.Align.CENTER)
            rb.connect("clicked", lambda _b, s=s: self._restore(s))
            db = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER,
                            css_classes=["flat"], tooltip_text=_("Delete"))
            db.connect("clicked", lambda _b, s=s: (backup.delete_snapshot(s),
                                                   self._fill_snapshots()))
            r.add_suffix(rb)
            r.add_suffix(db)
            self.snap_group.add(r)
            self.snap_rows.append(r)

    def _make_snapshot(self, *_a):
        entry = Gtk.Entry(text=_("Before changes"), hexpand=True)
        d = Adw.AlertDialog(heading=_("Create a restore point"), extra_child=entry)
        d.add_response("cancel", _("Cancel"))
        d.add_response("ok", _("Create"))
        d.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)

        def resp(_d, r):
            if r != "ok":
                return
            self.add_toast(Adw.Toast(title=_("Creating restore point…")))
            run_bg(lambda: backup.create_snapshot(self.app, entry.get_text() or "Manual"),
                   lambda _m: (self._fill_snapshots(),
                               self.add_toast(Adw.Toast(title=_("Restore point created")))),
                   lambda msg: self.add_toast(Adw.Toast(title=_(msg))))
        d.connect("response", resp)
        d.present(self)

    def _restore(self, snap):
        d = Adw.AlertDialog(
            heading=_("Go back to this restore point?"),
            body=_("The program is closed and everything since then is undone. Files "
                   "you saved in your own folders are kept."))
        d.add_response("cancel", _("Cancel"))
        d.add_response("ok", _("Restore"))
        d.set_response_appearance("ok", Adw.ResponseAppearance.DESTRUCTIVE)

        def resp(_d, r):
            if r != "ok":
                return

            def done(restored):
                self.app = restored
                self.restored = True
                self.close()
                self.window.refresh()
                self.window.toast(_("Restored"))
            run_bg(lambda: backup.restore_snapshot(self.app, snap), done,
                   lambda msg: self.add_toast(Adw.Toast(title=msg)))
        d.connect("response", resp)
        d.present(self)

    # ---- save
    def _save(self, *_a):
        if self.restored:
            return
        a = self.app
        a.name = self.name_row.get_text().strip() or a.name
        old_scale, old_proton = a.scale, a.proton
        a.scale = int(self.scale.get_value())
        a.scale_auto = self.scale_auto.get_active()
        a.autostart = self.autostart.get_active()
        a.tray_window = self.tray.get_active()
        a.launch_options_on = self.lo_on.get_active()
        a.args = self.args_row.get_text().strip()
        a.env = text_to_env(self.env_row.get_text())
        a.share_home = self.share.get_active()
        a.hide_z = self.hide_z.get_active()
        a.com_ports = {}
        for name, (cr, labels) in self.port_rows.items():
            i = cr.get_selected()
            if i > 0:
                a.com_ports[name] = (self.devs[i - 1][0] if i - 1 < len(self.devs)
                                     else labels[i])
        sel = self.proton_row.get_selected()
        a.proton = self.builds[sel - 1] if sel > 0 else ""
        a.graphics = GRAPHICS[self.gfx.get_selected()][0]
        a.ntsync = self.ntsync.get_active()
        a.wayland = self.wayland.get_active()
        a.debug = self.debug.get_active()
        a.gamemode = self.gamemode.get_active()
        a.mangohud = self.mangohud.get_active()
        a.fps_limit = int(self.fps.get_value())
        core.upsert_app(a)
        integration.write_desktop_files(a)
        window = self.window

        def apply():
            if a.proton != old_proton:
                backup.maybe_auto_snapshot(a, "Before changing the Proton version")
            if not core.is_running(a):
                devices.apply_all(a)
            if a.scale_auto:
                screens.apply_auto(a)
                return False
            if a.scale != old_scale:
                core.apply_scale(a)
            return a.scale != old_scale
        run_bg(apply, lambda changed: changed and window.toast(
            _("Scaling saved. Restart the program to see it.")))
        window.refresh()


# ------------------------------------------------------------ app dialogs --

class ProtonDialog(Adw.Dialog):
    def __init__(self, window: "MainWindow"):
        super().__init__(title=_("Proton versions"), content_width=480, content_height=520)
        self.window = window
        page = Adw.PreferencesPage()
        self.group = Adw.PreferencesGroup(
            description=_("Programs set to “Newest” use the newest version here. Pinned "
                          "programs keep theirs. Versions in use can't be deleted."))
        page.add(self.group)
        dl = Adw.PreferencesGroup()
        self.dl_row = row(_("Download the newest GE-Proton"), _("About 500 MB"))
        self.dl_btn = Gtk.Button(label=_("Download"), valign=Gtk.Align.CENTER,
                                 css_classes=["suggested-action"])
        self.dl_btn.connect("clicked", self._download)
        self.dl_row.add_suffix(self.dl_btn)
        self.bar = Gtk.ProgressBar(visible=False, valign=Gtk.Align.CENTER, width_request=120)
        self.dl_row.add_suffix(self.bar)
        dl.add(self.dl_row)
        page.add(dl)
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        view.set_content(page)
        self.set_child(view)
        self.rows = []
        self._fill()

    def _fill(self):
        for r in self.rows:
            self.group.remove(r)
        self.rows = []
        apps = core.load_apps()
        newest = proton.newest_ge()
        for name in proton.installed():
            users = [a.name for a in apps
                     if a.proton == name or (not a.proton and name == newest)]
            sub = (_("Used by: {list}").format(list=", ".join(users)) if users
                   else _("Not used"))
            if name == newest:
                sub = _("Newest") + "  ·  " + sub
            r = row(name, sub)
            if not users:
                b = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER,
                               css_classes=["flat"], tooltip_text=_("Delete"))
                b.connect("clicked", lambda _b, n=name: (proton.remove(n), self._fill()))
                r.add_suffix(b)
            self.group.add(r)
            self.rows.append(r)
        if not self.rows:
            r = row(_("None yet"), _("The first install downloads one automatically."))
            self.group.add(r)
            self.rows.append(r)

    def _download(self, *_a):
        self.dl_btn.set_sensitive(False)
        self.bar.set_visible(True)

        def progress(text, frac):
            GLib.idle_add(lambda: (self.bar.set_fraction(frac), self.dl_row.set_subtitle(text)))

        def work():
            rel = proton.latest_release()
            if rel["name"] in proton.installed():
                return None
            return proton.download(rel, progress)

        def done(name):
            self.bar.set_visible(False)
            self.dl_btn.set_sensitive(True)
            self.dl_row.set_subtitle(_("Downloaded {name}").format(name=name) if name
                                     else _("You already have the newest version."))
            self._fill()

        def err(msg):
            self.bar.set_visible(False)
            self.dl_btn.set_sensitive(True)
            self.dl_row.set_subtitle(msg)
        run_bg(work, done, err)


class DiskDialog(Adw.Dialog):
    """How much space everything takes, with clean-up buttons."""

    def __init__(self, window: "MainWindow"):
        super().__init__(title=_("Disk usage & clean-up"), content_width=560,
                         content_height=640)
        self.window = window
        self.page = Adw.PreferencesPage(description=_(
            "On Btrfs, copies share data, so the real total is often lower than the sum."))
        self.toasts = Adw.ToastOverlay(child=self.page)
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        view.set_content(self.toasts)
        self.set_child(view)
        self.groups = []
        self._load()

    def _load(self):
        for g in self.groups:
            self.page.remove(g)
        self.groups = []
        wait = Adw.PreferencesGroup()
        r = row(_("Measuring…"))
        r.add_prefix(spinner())
        wait.add(r)
        self.page.add(wait)
        self.groups.append(wait)

        def measure():
            return ([(a, maint.app_usage(a)) for a in core.load_apps()], maint.shared_usage())
        run_bg(measure, self._show)

    def _btn(self, label, fn, row_):
        b = Gtk.Button(label=label, valign=Gtk.Align.CENTER)

        def go(*_a):
            b.set_sensitive(False)
            run_bg(fn, lambda _r: (self.toasts.add_toast(Adw.Toast(title=_("Cleaned up"))),
                                   self._load()),
                   lambda msg: (self.toasts.add_toast(Adw.Toast(title=_(msg))),
                                b.set_sensitive(True)))
        b.connect("clicked", go)
        row_.add_suffix(b)

    def _show(self, res):
        apps, shared = res
        for g in self.groups:
            self.page.remove(g)
        self.groups = []
        H = maint.human
        for app, u in apps:
            g = Adw.PreferencesGroup(title=app.name)
            g.add(row(_("Program"), H(u["program"])))
            t = row(_("Temporary files"), H(u["temp"]))
            if u["temp"] > 1 << 20:
                self._btn(_("Clean"), lambda a=app: maint.clean_temp(a), t)
            g.add(t)
            sn = row(_("Restore points"), H(u["snapshots"]))
            if u["auto_snapshots"]:
                self._btn(_("Delete automatic ones"), lambda a=app: maint.clean_auto_snapshots(a), sn)
            g.add(sn)
            self.page.add(g)
            self.groups.append(g)
        g = Adw.PreferencesGroup(title=_("Shared"))
        g.add(row(_("Ready-made Windows environments"), H(shared["templates"]) + "  ·  "
                  + _("rebuilt automatically when needed")))
        g.add(row(_("Proton versions"), H(shared["proton"]) + "  ·  "
                  + _("delete unused ones under Proton versions")))
        g.add(row(_("Steam Runtime"), H(shared["runtime"])))
        dc = row(_("Downloaded components"), H(shared["downloads_cache"]) + "  ·  "
                 + _("only needed to rebuild the environments"))
        if shared["downloads_cache"]:
            self._btn(_("Clean"), maint.clean_downloads_cache, dc)
        g.add(dc)
        lg = row(_("Logs"), H(shared["logs"]))
        if shared["logs"]:
            self._btn(_("Clean"), maint.clean_logs, lg)
        g.add(lg)
        self.page.add(g)
        self.groups.append(g)


class HealthDialog(Adw.Dialog):
    ICONS = {health.OK: "emblem-ok-symbolic", health.WARN: "dialog-warning-symbolic",
             health.BAD: "dialog-error-symbolic"}

    def __init__(self, window: "MainWindow"):
        super().__init__(title=_("Health check"), content_width=520, content_height=600)
        self.page = Adw.PreferencesPage()
        self.group = Adw.PreferencesGroup()
        self.page.add(self.group)
        spin = row(_("Checking…"))
        spin.add_prefix(spinner())
        self.group.add(spin)
        self.spin = spin
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        self.toasts = Adw.ToastOverlay(child=self.page)
        view.set_content(self.toasts)
        self.set_child(view)
        run_bg(health.run_checks, self._show)

    def _show(self, checks):
        self.group.remove(self.spin)
        for c in checks:
            r = row(_(c.title), _(c.detail), subtitle_lines=0)
            r.add_prefix(Gtk.Image(icon_name=self.ICONS[c.status]))
            if c.fix:
                b = Gtk.Button(icon_name="edit-copy-symbolic", valign=Gtk.Align.CENTER,
                               css_classes=["flat"], tooltip_text=c.fix)
                b.connect("clicked", lambda _b, f=c.fix: (
                    copy_text(self, f),
                    self.toasts.add_toast(Adw.Toast(title=_("Command copied. Paste it in "
                                                            "a terminal.")))))
                r.add_suffix(b)
            self.group.add(r)


class PreferencesDialog(Adw.PreferencesDialog):
    def __init__(self, window: "MainWindow"):
        super().__init__(title=_("Preferences"))
        self.window = window
        s = core.load_settings()
        page = Adw.PreferencesPage()
        g = Adw.PreferencesGroup(title=_("New programs"))
        self.scale = scale_row(s["default_scale"])
        self.scale.set_title(_("Default display scaling"))
        g.add(self.scale)
        self.prebuild = Adw.SwitchRow(
            title=_("Prepare Windows environments in the background"),
            subtitle=_("The one-time .NET setup (~15 min) happens before you need it."))
        self.prebuild.set_active(s["prebuild"])
        g.add(self.prebuild)
        page.add(g)
        b = Adw.PreferencesGroup(title=_("Safety"))
        self.auto = Adw.SwitchRow(
            title=_("Automatic restore points"),
            subtitle=_("Before running another program in an app (installers, updates) and "
                       "before changing its Proton version. The last 3 are kept."))
        self.auto.set_active(s["auto_snapshots"])
        b.add(self.auto)
        page.add(b)
        p = Adw.PreferencesGroup(title=_("Install speed (experimental)"))
        self.nocow = Adw.SwitchRow(
            title=_("Faster writes on Btrfs"),
            subtitle=_("Turns off copy-on-write for new programs. Uses more disk space; "
                       "copies are no longer instant."))
        self.nocow.set_active(s["nocow"])
        p.add(self.nocow)
        self.speed = Adw.SwitchRow(
            title=_("Installer speed mode"),
            subtitle=_("Skips the constant “save to disk now” calls during installs. Much "
                       "faster, but a crash mid-install can damage that install. "
                       "Needs libeatmydata."))
        self.speed.set_active(s["speed_mode"])
        self.speed.set_sensitive(bool(core.EATMYDATA) or s["speed_mode"])
        p.add(self.speed)
        page.add(p)
        lg = Adw.PreferencesGroup()
        self.langs = ["auto", "en", "hu"]
        self.lang = Adw.ComboRow(title=_("Language"),
                                 subtitle=_("Takes effect after restarting Fröccs."),
                                 model=string_list([_("Automatic (system language)"),
                                                    "English", "Magyar"]))
        self.lang.set_selected(self.langs.index(s.get("language", "auto"))
                               if s.get("language", "auto") in self.langs else 0)
        lg.add(self.lang)
        page.add(lg)
        self.add(page)
        self.connect("closed", self._save)

    def _save(self, *_a):
        s = core.load_settings()
        s.update(default_scale=int(self.scale.get_value()), prebuild=self.prebuild.get_active(),
                 auto_snapshots=self.auto.get_active(), nocow=self.nocow.get_active(),
                 speed_mode=self.speed.get_active(),
                 language=self.langs[self.lang.get_selected()])
        core.save_settings(s)
        if s["prebuild"]:
            self.window.start_prebuild()


# ------------------------------------------------------------ main window --

class MainWindow(Adw.ApplicationWindow):
    def __init__(self, application):
        super().__init__(application=application, title="Fröccs",
                         default_width=680, default_height=620)
        self.toasts = Adw.ToastOverlay()
        settings = core.load_settings()

        add_btn = Gtk.Button(icon_name="list-add-symbolic", tooltip_text=_("Add software"))
        add_btn.connect("clicked", self.on_add)
        header = Adw.HeaderBar()
        header.pack_start(add_btn)
        # Background preparation: small labelled button, details on click
        prep_box = Gtk.Box(spacing=6)
        prep_box.append(spinner())
        self.prep_label = Gtk.Label(label=_("Preparing…"), css_classes=["caption"])
        prep_box.append(self.prep_label)
        self.prep_step = Gtk.Label(wrap=True, xalign=0, max_width_chars=36,
                                   css_classes=["heading"])
        self.prep_bar = Gtk.ProgressBar(show_text=True)
        why = Gtk.Label(wrap=True, xalign=0, max_width_chars=36, css_classes=["dim-label"],
                        label=_(PREP_WHY))
        pop_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_top=12,
                          margin_bottom=12, margin_start=12, margin_end=12)
        pop_box.append(Gtk.Label(label=_("Preparing Windows environments"), xalign=0,
                                 css_classes=["title-4"]))
        for w in (self.prep_step, self.prep_bar, why):
            pop_box.append(w)
        self.prep_btn = Gtk.MenuButton(child=prep_box, visible=False, css_classes=["flat"],
                                       popover=Gtk.Popover(child=pop_box))
        header.pack_start(self.prep_btn)

        menu = Gio.Menu()
        sec1 = Gio.Menu()
        sec1.append(_("Import a Fröccs file…"), "win.import-file")
        sec1.append(_("Import an existing Wine or Steam folder…"), "win.import-folder")
        sec1.append(_("Update Fröccs from a file…"), "win.update-file")
        sec2 = Gio.Menu()
        sec2.append(_("Disk usage & clean-up"), "win.disk")
        sec2.append(_("Proton versions"), "win.proton")
        sec2.append(_("Health check"), "win.health")
        sec2.append(_("Preferences"), "win.prefs")
        sec2.append(_("About Fröccs"), "win.about")
        menu.append_section(None, sec1)
        menu.append_section(None, sec2)
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu,
                                       tooltip_text=_("Menu")))
        self.view_btn = Gtk.ToggleButton(icon_name="view-grid-symbolic",
                                         tooltip_text=_("Grid view"),
                                         active=settings.get("view") == "grid")
        self.view_btn.connect("toggled", self._toggle_view)
        header.pack_end(self.view_btn)
        self.search_btn = Gtk.ToggleButton(icon_name="system-search-symbolic",
                                           tooltip_text=_("Search"))
        header.pack_end(self.search_btn)
        self.search = Gtk.SearchEntry(hexpand=True,
                                      placeholder_text=_("Search your programs"))
        self.search.connect("search-changed", lambda *_a: self.refresh())
        self.search_bar = Gtk.SearchBar(child=self.search, show_close_button=True)
        self.search_bar.connect_entry(self.search)
        self.search_bar.set_key_capture_widget(self)     # just start typing to search
        self.search_btn.bind_property("active", self.search_bar, "search-mode-enabled",
                                      GObject.BindingFlags.BIDIRECTIONAL)

        self.banner = Adw.Banner()
        self.update_banner = Adw.Banner(button_label=_("Update"))
        self.update_banner.connect("button-clicked", self._do_update)
        self.group = Adw.PreferencesGroup()
        self.list_page = Adw.PreferencesPage()
        self.list_page.add(self.group)
        self.flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True,
                                max_children_per_line=8, min_children_per_line=2,
                                valign=Gtk.Align.START, margin_top=18, margin_bottom=18,
                                margin_start=18, margin_end=18, row_spacing=12,
                                column_spacing=12)
        self.grid_page = Gtk.ScrolledWindow(child=self.flow,
                                            hscrollbar_policy=Gtk.PolicyType.NEVER)

        big_add = Gtk.Button(label=_("Add software"), halign=Gtk.Align.CENTER,
                             css_classes=["suggested-action", "pill"])
        big_add.connect("clicked", self.on_add)
        self.empty = Adw.StatusPage(
            icon_name=APP_ID, title=_("No software yet"),
            description=_("Pick a Windows installer (.exe or .msi), or drop it here.\n"
                          "Fröccs installs it and puts it in your apps menu."),
            child=big_add)

        self.stack = Gtk.Stack(vexpand=True)
        self.stack.add_named(self.empty, "empty")
        self.stack.add_named(self.list_page, "list")
        self.stack.add_named(self.grid_page, "grid")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(self.search_bar)
        box.append(self.update_banner)
        box.append(self.banner)
        box.append(self.stack)
        view = Adw.ToolbarView()
        view.add_top_bar(header)
        view.set_content(box)
        self.toasts.set_child(view)
        self.set_content(self.toasts)

        drop = Gtk.DropTarget.new(Gio.File, Gdk.DragAction.COPY)
        drop.connect("drop", self._on_drop)
        self.add_controller(drop)

        for name, cb in (("launch", self.a_launch), ("settings", self.a_settings),
                         ("other", self.a_other), ("link", self.a_link),
                         ("drive", self.a_drive), ("snapshot", self.a_snapshot),
                         ("export", self.a_export), ("stop", self.a_stop),
                         ("fonts", self.a_fonts),
                         ("log", self.a_log), ("remove", self.a_remove)):
            act = Gio.SimpleAction.new(name, GLib.VariantType.new("s"))
            act.connect("activate", cb)
            self.add_action(act)
        for name, cb in (("import-file", self.a_import_file),
                         ("import-folder", self.a_import_folder),
                         ("proton", lambda *_a: ProtonDialog(self).present(self)),
                         ("health", lambda *_a: HealthDialog(self).present(self)),
                         ("disk", lambda *_a: DiskDialog(self).present(self)),
                         ("update-file", self.a_update_file),
                         ("prefs", lambda *_a: PreferencesDialog(self).present(self)),
                         ("about", self.a_about)):
            act = Gio.SimpleAction.new(name, None)
            act.connect("activate", cb)
            self.add_action(act)

        self.rows: list[Gtk.Widget] = []
        self._prebuilding = False
        self.check_deps()
        self.refresh()
        run_bg(self._rescan_all)
        run_bg(devices.clean_documents_clutter)
        self.start_prebuild()
        run_bg(updater.find_update, self._offer_update)

    # -- helpers
    def toast(self, text):
        self.toasts.add_toast(Adw.Toast(title=text))
        return False

    def notify_done(self, title, body):
        """Desktop notification (handy when you walked away from a long install)."""
        n = Gio.Notification.new(title)
        n.set_body(body)
        self.get_application().send_notification(None, n)

    def check_deps(self):
        required, optional = core.missing_dependencies()
        if required:
            self.banner.set_title(_("umu-launcher is missing. Run install.sh again (without sudo)."))
            self.banner.set_revealed(True)
        elif optional:
            self.banner.set_title(_("Tip: install icoutils to get program icons ({cmd})")
                                  .format(cmd=distro.install_hint("icons")))
            self.banner.set_revealed(True)

    @staticmethod
    def _rescan_all():
        """Keep login links and file types current (apps add/move them on update)."""
        for app in core.load_apps():
            try:
                if integration.rescan(app):
                    core.upsert_app(app)
            except Exception:
                pass

    def start_prebuild(self):
        s = core.load_settings()
        if not s.get("prebuild") or not core.umu_path() or self._prebuilding:
            return
        if all(core.template_ready(k) for k in core.TEMPLATES):
            return
        self._prebuilding = True
        self.prep_btn.set_visible(True)

        def show(text, frac):
            pct = int(frac * 100)
            step = text.split("\n")[0]
            self.prep_label.set_text(_("Preparing… {pct} %").format(pct=pct))
            self.prep_step.set_text(step)
            self.prep_bar.set_fraction(frac)
            self.prep_bar.set_text(f"{pct} %")
            self.prep_btn.set_tooltip_text(
                _("Preparing Windows environments ({pct} %)").format(pct=pct) + "\n"
                + _("Now: {step}").format(step=step) + "\n\n" + _(PREP_WHY))
            return False

        show(_("Starting…"), 0.0)

        def progress(text, frac):
            GLib.idle_add(show, text, frac)

        def done(_r=None):
            self._prebuilding = False
            self.prep_btn.set_visible(False)
            self.prep_btn.get_popover().popdown()
            self.toast(_("Windows environments are ready: new programs install faster now"))

        def failed(msg):
            self._prebuilding = False
            self.prep_btn.set_visible(False)
            self.toast(_("Background preparation stopped: {msg}").format(msg=msg))
        run_bg(lambda: core.prebuild_templates(progress), done, failed)

    def _menu_for(self, app: core.App) -> Gio.Menu:
        menu = Gio.Menu()
        s1 = Gio.Menu()
        for label, act in ((_("Start"), "launch"), (_("Settings"), "settings")):
            s1.append(label, f"win.{act}::{app.id}")
        s2 = Gio.Menu()
        for label, act in ((_("Run another .exe here…"), "other"),
                           (_("Paste a login link…"), "link"),
                           (_("Open C: drive"), "drive"),
                           (_("Install fonts…"), "fonts"),
                           (_("Create restore point"), "snapshot"),
                           (_("Export…"), "export")):
            s2.append(label, f"win.{act}::{app.id}")
        s3 = Gio.Menu()
        for label, act in ((_("Force stop"), "stop"), (_("Show log"), "log"),
                           (_("Remove"), "remove")):
            s3.append(label, f"win.{act}::{app.id}")
        for s in (s1, s2, s3):
            menu.append_section(None, s)
        return menu

    def refresh(self):
        for r in self.rows:
            self.group.remove(r)
        self.rows = []
        while child := self.flow.get_first_child():
            self.flow.remove(child)
        all_apps = core.load_apps()
        query = self.search.get_text().strip().lower()
        apps = [a for a in all_apps if query in a.name.lower()] if query else all_apps
        grid = self.view_btn.get_active()
        self.stack.set_visible_child_name(("grid" if grid else "list") if all_apps else "empty")
        for a in apps:
            # list row
            sub = (_("auto") + " " if a.scale_auto else "") + f"{a.scale} %" + (
                f"  ·  {a.proton}" if a.proton else "")
            r = row(a.name, sub)
            r.add_prefix(app_icon(a, 40))
            play = Gtk.Button(icon_name="media-playback-start-symbolic",
                              valign=Gtk.Align.CENTER, tooltip_text=_("Start"),
                              css_classes=["flat"], action_name="win.launch",
                              action_target=GLib.Variant("s", a.id))
            more = Gtk.MenuButton(icon_name="view-more-symbolic", menu_model=self._menu_for(a),
                                  valign=Gtk.Align.CENTER, css_classes=["flat"])
            r.add_suffix(play)
            r.add_suffix(more)
            r.set_activatable_widget(play)
            self.group.add(r)
            self.rows.append(r)
            # grid tile
            tile = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            tile.append(app_icon(a, 72))
            tile.append(Gtk.Label(label=a.name, wrap=True, justify=Gtk.Justification.CENTER,
                                  max_width_chars=14, lines=2,
                                  ellipsize=Pango.EllipsizeMode.END))
            btn = Gtk.Button(child=tile, css_classes=["flat"], width_request=120,
                             tooltip_text=_("Click to start, right-click for more"),
                             action_name="win.launch", action_target=GLib.Variant("s", a.id))
            pop = Gtk.PopoverMenu.new_from_model(self._menu_for(a))
            pop.set_parent(btn)
            click = Gtk.GestureClick(button=3)
            click.connect("pressed", lambda *_x, p=pop: p.popup())
            btn.add_controller(click)
            self.flow.append(btn)

    def _toggle_view(self, btn):
        s = core.load_settings()
        s["view"] = "grid" if btn.get_active() else "list"
        core.save_settings(s)
        self.refresh()

    # -- add / import
    def on_add(self, *_a):
        if core.missing_dependencies()[0]:
            self.toast(_("Install umu-launcher first (see the bar at the top)"))
            return
        pick_file(self, _("Choose a Windows installer"), ["*.exe", "*.msi"],
                  downloads_dir(), self.open_file)

    def open_file(self, path: Path):
        """Entry point for the file chooser, drag & drop and “Open with Fröccs”."""
        if path.suffix.lower() == EXPORT_SUFFIX:
            self.import_file(path)
        elif path.suffix.lower() in (".exe", ".msi"):
            AddDialog(self, path).present(self)
        else:
            self.toast(_("That's not a Windows program (.exe or .msi)"))

    def _on_drop(self, _target, value, _x, _y):
        if isinstance(value, Gio.File) and value.get_path():
            self.open_file(Path(value.get_path()))
            return True
        return False

    def a_import_file(self, *_a):
        pick_file(self, _("Choose a Fröccs file"), [f"*{EXPORT_SUFFIX}"], None,
                  self.import_file)

    def import_file(self, path: Path):
        self.toast(_("Importing… this can take a few minutes"))
        run_bg(lambda: backup.import_app(path, lambda *_x: None),
               lambda app: (self.refresh(),
                            self.toast(_("{name} is now in your apps menu").format(name=app.name))),
               lambda msg: self.toast(msg))

    def a_import_folder(self, *_a):
        steam = Path.home() / ".local/share/Steam/steamapps/compatdata"

        def chosen(folder: Path):
            name = folder.name if not folder.name.isdigit() else _("Imported program")
            run_bg(lambda: backup.import_prefix(folder, name, lambda *_x: None),
                   lambda res: AddDialog(self, imported=res).present(self),
                   lambda msg: self.toast(msg))
        pick_folder(self, _("Choose the Wine prefix folder"), chosen,
                    steam if steam.is_dir() else Path.home())

    def export_app(self, app: core.App):
        dlg = Gtk.FileDialog(title=_("Export {name}").format(name=app.name),
                             initial_name=f"{app.name}{EXPORT_SUFFIX}")

        def done(d, res):
            try:
                dest = Path(d.save_finish(res).get_path())
            except GLib.Error:
                return
            self.toast(_("Exporting… this can take a few minutes"))
            run_bg(lambda: backup.export_app(app, dest, lambda *_x: None),
                   lambda _r: self.toast(_("Exported to {path}").format(path=dest.name)),
                   lambda msg: self.toast(_(msg)))
        dlg.save(self, None, done)

    # -- per-app actions (parameter = app id)
    def _app(self, param):
        return core.get_app(param.get_string())

    def a_launch(self, _a, param):
        if app := self._app(param):
            core.launch_detached(app, user_args=True)
            core.schedule_rescan(app)
            self.toast(_("Starting {name}…").format(name=app.name))

    def a_settings(self, _a, param):
        if app := self._app(param):
            SettingsDialog(self, app).present(self)

    def a_other(self, _a, param):
        app = self._app(param)
        if not app:
            return

        def chosen(path: Path):
            def run():
                made = backup.maybe_auto_snapshot(app, "Before running another program")
                if path.suffix.lower() == ".msi":
                    core.launch_detached(app, "msiexec", ["/i", str(path)])
                else:
                    core.launch_detached(app, str(path))
                return made
            run_bg(run, lambda made: self.toast(
                _("Restore point made, starting the program…") if made
                else _("Starting the program…")))
        pick_file(self, _("Run a program in this app's environment"), ["*.exe", "*.msi"],
                  drive_c(app.prefix), chosen)

    def a_link(self, _a, param):
        """Fallback when the browser can't hand the login link over by itself."""
        app = self._app(param)
        if not app:
            return
        entry = Gtk.Entry(placeholder_text="adskidmgr:/login?code=…", hexpand=True)
        d = Adw.AlertDialog(
            heading=_("Paste the login link"),
            body=_("After signing in, the browser tries to open a link like "
                   "“adskidmgr:/login?code=…”. If nothing happened, copy that link (from "
                   "the browser's console, F12) and paste it here. {name} must be running.")
            .format(name=app.name),
            extra_child=entry)
        d.add_response("cancel", _("Cancel"))
        d.add_response("open", _("Log in"))
        d.set_response_appearance("open", Adw.ResponseAppearance.SUGGESTED)

        def on_resp(_d, resp):
            if resp != "open" or not entry.get_text().strip():
                return
            try:
                integration.register_protocols(app)
                core.upsert_app(app)
                integration.launch_url_detached(app, entry.get_text())
                self.toast(_("Login link sent"))
            except core.InstallError as e:
                self.toast(str(e))
        d.connect("response", on_resp)
        d.present(self)

    def a_drive(self, _a, param):
        if app := self._app(param):
            open_path(drive_c(app.prefix))

    def a_snapshot(self, _a, param):
        if app := self._app(param):
            self.toast(_("Creating restore point…"))
            run_bg(lambda: backup.create_snapshot(app, "Manual"),
                   lambda _m: self.toast(_("Restore point created")),
                   lambda msg: self.toast(_(msg)))

    def a_export(self, _a, param):
        if app := self._app(param):
            self.export_app(app)

    def a_stop(self, _a, param):
        if app := self._app(param):
            n = core.kill_prefix(app.prefix)
            self.toast(_("{name} stopped").format(name=app.name) if n
                       else _("{name} isn't running").format(name=app.name))

    def a_log(self, _a, param):
        if app := self._app(param):
            if app.log_file.exists():
                open_path(app.log_file)
            else:
                self.toast(_("No log yet"))

    def a_remove(self, _a, param):
        app = self._app(param)
        if not app:
            return
        d = Adw.AlertDialog(
            heading=_("Remove {name}?").format(name=app.name),
            body=_("This deletes the program, its settings, its restore points and its C: "
                   "drive. Files in your own folders are kept."))
        d.add_response("cancel", _("Cancel"))
        d.add_response("remove", _("Remove"))
        d.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)

        def on_resp(_d, resp):
            if resp == "remove":
                run_bg(lambda: integration.remove_app(app),
                       lambda _r: (self.refresh(),
                                   self.toast(_("{name} removed").format(name=app.name))))
        d.connect("response", on_resp)
        d.present(self)

    def a_fonts(self, _a, param):
        app = self._app(param)
        if not app:
            return
        dlg = Gtk.FileDialog(title=_("Choose fonts for {name}").format(name=app.name))
        dlg.set_filters(file_filter(_("Fonts"), fonts.FONT_PATTERNS))

        def done(d, res):
            try:
                files = [Path(f.get_path()) for f in d.open_multiple_finish(res)]
            except GLib.Error:
                return
            run_bg(lambda: fonts.install_fonts(app, files),
                   lambda names: self.toast(_("Installed {n} font(s)").format(n=len(names))),
                   lambda msg: self.toast(_(msg)))
        dlg.open_multiple(self, None, done)

    # -- updates
    def _offer_update(self, found):
        if not found:
            return
        version, path = found
        self._update_path = path
        self.update_banner.set_title(_("Fröccs {version} is ready to install (found in {folder})")
                                     .format(version=version, folder=path.parent.name))
        self.update_banner.set_revealed(True)

    def a_update_file(self, *_a):
        def chosen(path: Path):
            if not updater.file_version(path):
                self.toast(_("That's not a Fröccs release file"))
                return
            self._update_path = path
            self._do_update()
        pick_file(self, _("Choose a Fröccs release file"), ["froccs-*.tar.gz"],
                  downloads_dir(), chosen)

    def _do_update(self, *_a):
        path = getattr(self, "_update_path", None)
        if not path:
            return
        self.update_banner.set_revealed(False)
        self.toast(_("Updating…"))

        def done(res):
            ok, output = res
            if not ok:
                d = Adw.AlertDialog(heading=_("Update failed"), body=output[-1500:])
                d.add_response("ok", _("OK"))
                d.present(self)
                return
            note = "\n".join(l for l in output.splitlines() if l.startswith("NOTE:"))
            d = Adw.AlertDialog(heading=_("Updated"),
                                body=_("Restart Fröccs to use the new version. Your "
                                       "programs were kept.") + ("\n\n" + note if note else ""))
            d.add_response("later", _("Later"))
            d.add_response("restart", _("Restart now"))
            d.set_response_appearance("restart", Adw.ResponseAppearance.SUGGESTED)
            d.connect("response", lambda _d, r: r == "restart" and (
                updater.restart(), self.get_application().quit()))
            d.present(self)
        run_bg(lambda: updater.install(path), done, lambda msg: self.toast(msg))

    def a_about(self, *_a):
        Adw.AboutDialog(application_name="Fröccs", application_icon=APP_ID,
                        version=__version__,
                        comments=_("Run Windows software on Linux with Proton."),
                        license_type=Gtk.License.MIT_X11).present(self)


class FroccsApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_OPEN)

    def _window(self) -> MainWindow:
        win = self.get_active_window()
        if win is None:
            Gtk.Window.set_default_icon_name(APP_ID)
            win = MainWindow(self)
        win.present()
        return win

    def do_activate(self):
        self._window()

    def do_open(self, files, _n_files, _hint):
        """“Open with Fröccs” on an .exe / .msi / .froccs file."""
        win = self._window()
        for f in files:
            if f.get_path():
                win.open_file(Path(f.get_path()))
                break


def main(argv: list[str] | None = None) -> int:
    return FroccsApp().run(argv or [])
