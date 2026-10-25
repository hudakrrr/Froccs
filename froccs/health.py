"""Health check: is everything Fröccs relies on in place? Each check says how to fix it."""
from __future__ import annotations

import glob
import grp
import os
import shutil
import subprocess
from dataclasses import dataclass

from . import core, devices, distro, integration, proton
from .i18n import _
from .paths import DATA_DIR

OK, WARN, BAD = "ok", "warn", "bad"


@dataclass
class Check:
    status: str
    title: str
    detail: str
    fix: str = ""        # a command to copy, if there is one


def _cmd(args: list[str]) -> str:
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


def run_checks() -> list[Check]:
    out: list[Check] = []
    out.append(Check(OK, _("System"), distro.name()
                     + (" · " + _("read-only system") if distro.immutable() else "")
                     + (" · " + _("unknown distribution: package hints may not fit")
                        if distro.family() == "unknown" else "")))

    umu = core.umu_path()
    out.append(Check(OK, "umu-launcher", umu) if umu else
               Check(BAD, "umu-launcher", "Missing: nothing can run without it.",
                     "Run install.sh again (without sudo)"))

    builds = proton.installed()
    out.append(Check(OK, "Proton", ", ".join(builds[:3]) + ("…" if len(builds) > 3 else ""))
               if builds else
               Check(WARN, "Proton", "None downloaded yet. The first install downloads "
                     "GE-Proton automatically (~500 MB)."))

    icds = glob.glob("/usr/share/vulkan/icd.d/*.json") + glob.glob("/etc/vulkan/icd.d/*.json")
    out.append(Check(OK, "Vulkan graphics driver", f"{len(icds)} driver(s) found") if icds else
               Check(BAD, "Vulkan graphics driver", "Not found. DXVK graphics need Vulkan.",
                     distro.install_hint("vulkan")))

    if core.ntsync_available():
        out.append(Check(OK, "NTSYNC", "Available. Can be turned on per app."))
    elif os.path.exists("/dev/ntsync"):
        out.append(Check(WARN, "NTSYNC", "Exists but your user can't open /dev/ntsync.",
                         "sudo chmod 666 /dev/ntsync"))
    else:
        out.append(Check(WARN, "NTSYNC", "Kernel module not loaded (optional, makes heavy "
                         "programs faster).",
                         "sudo modprobe ntsync && echo ntsync | sudo tee "
                         "/etc/modules-load.d/ntsync.conf"))

    _required, optional = core.missing_dependencies()
    out.append(Check(OK, "Program icons", "icoutils installed") if not optional else
               Check(WARN, "Program icons", "icoutils missing: shortcuts get a generic icon.",
                     distro.install_hint("icons")))

    profile = _cmd(["powerprofilesctl", "get"])
    if profile:
        out.append(Check(WARN if profile == "power-saver" else OK, "Power mode",
                         f"{profile}" + (": installs and programs run much slower"
                                         if profile == "power-saver" else ""),
                         "powerprofilesctl set performance" if profile == "power-saver" else ""))

    group = distro.serial_group()        # 'dialout', or 'uucp' on Arch
    in_dialout = False
    try:
        in_dialout = grp.getgrnam(group).gr_gid in os.getgroups()
    except KeyError:
        pass
    devs = devices.serial_devices()
    if in_dialout or devices.serial_permission_ok() and devs:
        out.append(Check(OK, "Serial ports (PLC cables)",
                         f"Access OK, {len(devs)} device(s) connected"))
    else:
        out.append(Check(WARN, "Serial ports (PLC cables)",
                         _("Your user isn't in the '{group}' group, so programs can't open "
                           "USB-serial adapters. Log out and in after the fix.").format(group=group),
                         f"sudo usermod -aG {group} $USER"))

    fs = _cmd(["stat", "-f", "-c", "%T", str(DATA_DIR if DATA_DIR.exists() else DATA_DIR.parent)])
    if fs:
        out.append(Check(OK, "File system", f"{fs}" + (": instant copies and restore points"
                                                      if fs == "btrfs" else
                                                      ": copies and restore points take "
                                                      "full disk space")))
    try:
        free = shutil.disk_usage(DATA_DIR if DATA_DIR.exists() else DATA_DIR.parent).free >> 30
        out.append(Check(OK if free >= 20 else WARN, "Free disk space", f"{free} GB"
                         + ("" if free >= 20 else ": big programs need 10-30 GB each")))
    except OSError:
        pass

    if core.load_settings().get("speed_mode"):
        out.append(Check(OK, "Installer speed mode", core.EATMYDATA) if core.EATMYDATA else
                   Check(WARN, "Installer speed mode", "On, but libeatmydata is missing, "
                         "so it does nothing.", distro.install_hint("eatmydata")))

    # Printing (plotting drawings): Wine prints through CUPS, Linux's print system
    sched = _cmd(["lpstat", "-r"])
    if not shutil.which("lpstat"):
        out.append(Check(WARN, _("Printing"), _("CUPS tools not found, so printing from "
                                                "programs won't work."),
                         distro.install_hint("cups") + " && sudo systemctl enable --now cups"))
    elif "not running" in sched or not sched:
        out.append(Check(WARN, _("Printing"), _("The print service (CUPS) isn't running."),
                         "sudo systemctl enable --now cups"))
    else:
        printers = [l.split()[1] for l in _cmd(["lpstat", "-p"]).splitlines()
                    if l.startswith("printer ") and len(l.split()) > 1]
        has_pdf = any("pdf" in p.lower() for p in printers)
        if printers:
            out.append(Check(OK if has_pdf else WARN, _("Printing"),
                             _("Printers programs can use: {list}").format(list=", ".join(printers))
                             + ("" if has_pdf else "\n" + _("No PDF printer: add one to "
                                                             "“print” drawings to PDF.")),
                             "" if has_pdf else distro.install_hint("cups-pdf")))
        else:
            out.append(Check(WARN, _("Printing"), _("No printers set up. A PDF printer lets "
                                                    "programs “print” to PDF files."),
                             distro.install_hint("cups-pdf")))

    # Gaming helpers (optional)
    missing = [n for n, c in (("gamemode", "gamemoderun"), ("mangohud", "mangohud"))
               if not shutil.which(c)]
    out.append(Check(OK, _("Game helpers"), _("GameMode and MangoHud are installed"))
               if not missing else
               Check(WARN, _("Game helpers"),
                     _("Optional, for games: {list} not installed.").format(list=", ".join(missing)),
                     distro.install_hint(*missing)))

    for app in core.load_apps():
        if not core.is_running(app):
            values = core.read_scale(app)
            if any(v != app.scale for v in values.values()):
                out.append(Check(WARN, _("{name}: display scaling").format(name=app.name),
                                 _("Set to {pct} % in Fröccs, but not applied inside the "
                                   "program's Windows environment.").format(pct=app.scale),
                                 f"froccs scale {app.id} {app.scale}"))
        for scheme in integration.find_protocols(app.prefix):
            linux = _cmd(["xdg-mime", "query", "default", f"x-scheme-handler/{scheme}"])
            if linux != integration._protocol_desktop(app, scheme).name:
                out.append(Check(WARN, f"{app.name}: {scheme}: links",
                                 "Your browser won't send login links to this program. "
                                 "Open its Settings → Advanced → Scan again.",
                                 f"froccs links {app.id}"))
    return out
