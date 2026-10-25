"""
froccs                          open the window
froccs <installer.exe|.froccs>   open the window with that file
froccs run <id> [--shortcut N] [files]   start an app (used by menu entries)
froccs list                     list installed apps
froccs add <installer> [--name N] [--scale 125] [--dotnet]
froccs scale <id> <percent>
froccs stop <id>                force-stop everything the app runs
froccs snapshot <id> [label]    create a restore point
froccs export <id> <file>       export to a .froccs file
froccs import <file>            import a .froccs file
froccs doctor                   health check
froccs url <id> <link>          open a login link (adskidmgr:/login?code=...)
froccs links <id>               re-scan login links and file types, show status
froccs refresh                  rebuild menu entries (run by install.sh on update)
froccs migrate                  move an older setup over (run by install.sh)
froccs remove <id>
"""
import argparse
import subprocess
import sys
from pathlib import Path

from . import analyze, backup, core, health, integration, recipes


def cli_add(ns) -> int:
    installer = Path(ns.installer).expanduser().resolve()
    if not installer.is_file():
        print(f"Not found: {installer}")
        return 1
    name = ns.name or core.guess_name(installer)
    a = analyze.analyze(installer)
    r = recipes.find(installer.name, name)
    app = core.App(id=core.make_id(name), name=name, scale=ns.scale,
                   dotnet=ns.dotnet or (r.dotnet if r else a.dotnet),
                   graphics=r.graphics if r else "dxvk",
                   dll_overrides=dict(r.dll_overrides) if r else {},
                   env=dict(r.env) if r else {},
                   file_types=[{"ext": e, "shortcut": -1} for e in (r.file_types if r else [])],
                   recipe=r.id if r else "")
    print(f"{a.kind}; uses: {', '.join(a.frameworks) or '-'}; .NET: {app.dotnet}")
    if r:
        print(f"Recognized: {r.name}")
        if r.warning:
            print(f"WARNING: {r.warning}")
    for w in a.warnings:
        print(f"WARNING: {w}")

    def progress(text, frac):
        print(f"[{frac * 100:3.0f}%] {text.splitlines()[0]}")

    try:
        cands, failed = core.install(app, installer, progress,
                                     extra_verbs=tuple(r.verbs) if r else (),
                                     reg_edits=tuple(tuple(e) for e in r.registry) if r else ())
    except Exception as e:
        core.discard(app)
        print(f"Failed: {e}")
        return 1
    if failed:
        print("Components that failed:", ", ".join(failed))
    if not cands:
        exe = input(f"No new program found. Path to the .exe (under {core.drive_c(app.prefix)}): ")
        exes = [Path(exe)]
    else:
        for i, c in enumerate(cands, 1):
            print(f"  {i}) {c}")
        pick = input("Which ones go in the apps menu? (e.g. 1 or 1,3) [1]: ").strip() or "1"
        exes = [cands[int(p) - 1] for p in pick.split(",") if p.strip().isdigit()]
    integration.finalize(app, exes)
    print(f"Added '{app.name}' to the apps menu (id: {app.id})")
    return 0


def main() -> int:
    argv = sys.argv[1:]
    if not argv or (len(argv) == 1 and Path(argv[0]).suffix.lower()
                    in (".exe", ".msi", ".froccs") and Path(argv[0]).exists()):
        from .gui import main as gui_main
        return gui_main([sys.argv[0], *argv])

    p = argparse.ArgumentParser(prog="froccs")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("id")
    r.add_argument("--shortcut", type=int, default=-1)
    r.add_argument("files", nargs="*")
    sub.add_parser("list")
    sub.add_parser("refresh")
    sub.add_parser("migrate")
    sub.add_parser("doctor")
    a = sub.add_parser("add")
    a.add_argument("installer")
    a.add_argument("--name")
    a.add_argument("--scale", type=int, default=core.load_settings()["default_scale"])
    a.add_argument("--dotnet", action="store_true")
    for cmd in ("stop", "remove", "links"):
        sub.add_parser(cmd).add_argument("id")
    s = sub.add_parser("scale")
    s.add_argument("id")
    s.add_argument("percent", type=int, nargs="?")
    sn = sub.add_parser("snapshot")
    sn.add_argument("id")
    sn.add_argument("label", nargs="?", default="Manual")
    ex = sub.add_parser("export")
    ex.add_argument("id")
    ex.add_argument("file")
    im = sub.add_parser("import")
    im.add_argument("file")
    u = sub.add_parser("url")
    u.add_argument("id")
    u.add_argument("link")
    ns = p.parse_args(argv)

    if ns.cmd == "add":
        return cli_add(ns)
    if ns.cmd == "list":
        for app in core.load_apps():
            print(f"{app.id:40} {app.scale:>4}%  {app.name}")
        return 0
    if ns.cmd == "migrate":
        from . import migrate
        try:
            for line in migrate.migrate():
                print(f"  {line}")
        except migrate.MigrationError as e:
            print(f"ERROR: {e}")
            return 1
        return 0
    if ns.cmd == "refresh":
        # After an update: rebuild menu entries etc. with the new code.
        # Installed programs (prefixes) are not touched.
        import shutil
        for old in ("general", "autodesk", "plc"):          # 0.2 per-type templates
            shutil.rmtree(core.TEMPLATE_DIR / old, ignore_errors=True)
        for app in core.load_apps():
            integration.register_protocols(app)
            integration.merge_detected_file_types(app)
            integration.write_desktop_files(app)
            core.upsert_app(app)
            fixed = ""
            if not core.is_running(app):
                from . import devices
                devices.apply_tray(app)             # hide Wine's tray window (0.3.2)
            if not core.is_running(app) and any(
                    v != app.scale for v in core.read_scale(app).values()):
                core.apply_scale(app)               # 0.3.0 and older missed a key
                fixed = f" (display scaling {app.scale} % re-applied)"
            print(f"  kept: {app.name}{fixed}")
        return 0
    if ns.cmd == "doctor":
        icons = {health.OK: "✔", health.WARN: "!", health.BAD: "✘"}
        for c in health.run_checks():
            print(f" {icons[c.status]} {c.title}: {c.detail}")
            if c.fix:
                print(f"     fix: {c.fix}")
        return 0
    if ns.cmd == "import":
        app = backup.import_app(Path(ns.file).expanduser(), lambda t, f: print(t))
        print(f"Imported '{app.name}' (id: {app.id})")
        return 0

    app = core.get_app(ns.id)
    if not app:
        print(f"No app with id '{ns.id}'. See: froccs list")
        return 1
    if ns.cmd == "run":
        core.launch_shortcut(app, ns.shortcut, ns.files)      # does not return
    elif ns.cmd == "url":
        try:
            integration.launch_url(app, ns.link)                # does not return
        except core.InstallError as e:
            print(e)
            subprocess.run(["notify-send", "Fröccs", str(e)])
            return 1
    elif ns.cmd == "links":
        integration.rescan(app)
        core.upsert_app(app)
        print(f"{app.name} handles: {', '.join(app.protocols) or 'no web links'}")
        print(f"File types: {', '.join('.' + f['ext'] for f in app.file_types) or '-'}")
        for scheme, cmd in integration.find_protocols(app.prefix).items():
            exe = integration.win_to_linux(app.prefix, integration.split_command(cmd, "x")[0])
            linux = subprocess.run(["xdg-mime", "query", "default", f"x-scheme-handler/{scheme}"],
                                   capture_output=True, text=True).stdout.strip()
            ok = linux == integration._protocol_desktop(app, scheme).name and exe.exists()
            print(f"\n  {scheme}:")
            print(f"    Windows handler : {exe}")
            print(f"    exists          : {'yes' if exe.exists() else 'NO'}")
            print(f"    Linux sends to  : {linux or 'NOTHING (not registered)'}")
            print(f"    status          : {'OK' if ok else 'PROBLEM'}")
    elif ns.cmd == "scale":
        if ns.percent:
            app.scale = ns.percent
            core.upsert_app(app)
            core.apply_scale(app)
        print(f"{app.name}: Fröccs setting {app.scale} %")
        for where, pct in core.read_scale(app).items():
            print(f"  {where:30} {str(pct) + ' %' if pct else 'not set'}")
        if core.is_running(app):
            print("  (the program is running: the files update when it closes)")
    elif ns.cmd == "stop":
        print(f"Stopped {core.kill_prefix(app.prefix)} process(es)")
    elif ns.cmd == "snapshot":
        backup.create_snapshot(app, ns.label)
        print("Restore point created")
    elif ns.cmd == "export":
        backup.export_app(app, Path(ns.file).expanduser(), lambda t, f: print(t))
    elif ns.cmd == "remove":
        integration.remove_app(app)
        print(f"Removed {app.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
