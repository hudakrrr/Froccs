"""
Which Linux distribution is this, and how do you install things on it?

Every distro describes itself in /etc/os-release: ID=fedora, ID=ubuntu,
and ID_LIKE lists its parents (Linux Mint says ID_LIKE="ubuntu debian").
That's enough to pick the package manager and the package names, which
differ: GTK 4 for Python is "python3-gobject" on Fedora, "python3-gi" +
"gir1.2-gtk-4.0" on Debian/Ubuntu, "python-gobject" on Arch.

Run as a script, it prints install plans for install.sh:
    python3 -m froccs.distro plan gui icons desktop tools
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from functools import lru_cache

FAMILIES = ("fedora", "debian", "arch", "suse")

# logical name -> {family: [packages]}; "*" = same name everywhere
PACKAGES = {
    "gui": {"fedora": ["python3-gobject", "gtk4", "libadwaita"],
            "debian": ["python3-gi", "gir1.2-gtk-4.0", "gir1.2-adw-1"],
            "arch": ["python-gobject", "gtk4", "libadwaita"],
            "suse": ["python3-gobject", "typelib-1_0-Gtk-4_0", "typelib-1_0-Adw-1"]},
    "icons": {"*": ["icoutils"]},
    "desktop": {"*": ["desktop-file-utils", "xdg-utils", "shared-mime-info"]},
    "tools": {"*": ["curl", "tar", "zstd"]},
    "vulkan": {"fedora": ["mesa-vulkan-drivers"], "debian": ["mesa-vulkan-drivers"],
               "arch": ["vulkan-radeon", "vulkan-intel"],
               "suse": ["libvulkan_radeon", "libvulkan_intel"]},
    "cups": {"*": ["cups"]},
    "cups-pdf": {"debian": ["printer-driver-cups-pdf"], "*": ["cups-pdf"]},
    "gamemode": {"*": ["gamemode"]},
    "mangohud": {"*": ["mangohud"]},
    "eatmydata": {"debian": ["eatmydata"], "*": ["libeatmydata"]},
}

PRETTY = {"fedora": "Fedora", "debian": "Debian/Ubuntu", "arch": "Arch", "suse": "openSUSE"}


@lru_cache(maxsize=1)
def os_release() -> dict:
    out = {}
    for path in ("/etc/os-release", "/usr/lib/os-release"):
        try:
            for line in open(path, encoding="utf-8"):
                if "=" in line:
                    k, v = line.rstrip("\n").split("=", 1)
                    out[k] = v.strip().strip('"')
            break
        except OSError:
            continue
    return out


@lru_cache(maxsize=1)
def family() -> str:
    rel = os_release()
    ids = [rel.get("ID", "")] + rel.get("ID_LIKE", "").split()
    for i in ids:
        i = i.lower()
        if i in ("fedora", "rhel", "centos", "nobara", "bazzite", "ultramarine"):
            return "fedora"
        if i in ("debian", "ubuntu", "linuxmint", "pop", "elementary", "zorin", "neon"):
            return "debian"
        if i in ("arch", "manjaro", "endeavouros", "cachyos", "garuda", "steamos"):
            return "arch"
        if i.startswith("opensuse") or i in ("suse", "sles"):
            return "suse"
    return "unknown"


def name() -> str:
    return os_release().get("PRETTY_NAME") or PRETTY.get(family(), "Linux")


def immutable() -> bool:
    """Fedora Silverblue/Kinoite/Bazzite and similar: the system is read-only."""
    return os.path.exists("/run/ostree-booted")


def packages(*logical: str) -> list[str]:
    fam = family()
    out: list[str] = []
    for item in logical:
        table = PACKAGES.get(item, {"*": [item]})
        # unknown distro: use the common (Fedora/upstream) names as a hint
        for p in table.get(fam, table.get("*", table.get("fedora", []))):
            if p not in out:
                out.append(p)
    return out


def is_installed(pkg: str) -> bool:
    fam = family()
    try:
        if fam in ("fedora", "suse") and shutil.which("rpm"):
            return subprocess.run(["rpm", "-q", pkg], capture_output=True).returncode == 0
        if fam == "debian" and shutil.which("dpkg-query"):
            r = subprocess.run(["dpkg-query", "-W", "-f=${Status}", pkg],
                               capture_output=True, text=True)
            return "install ok installed" in r.stdout
        if fam == "arch" and shutil.which("pacman"):
            return subprocess.run(["pacman", "-Qq", pkg], capture_output=True).returncode == 0
    except OSError:
        pass
    return True          # unknown distro: don't nag, the runtime checks will tell


def missing(*logical: str) -> list[str]:
    return [p for p in packages(*logical) if not is_installed(p)]


def install_command(pkgs: list[str]) -> str:
    """The command a user would type to install these packages."""
    joined = " ".join(pkgs)
    fam = family()
    if fam == "fedora":
        if immutable():
            return f"sudo rpm-ostree install --apply-live {joined}"
        return f"sudo dnf install {joined}"
    if fam == "debian":
        return f"sudo apt install {joined}"
    if fam == "arch":
        return f"sudo pacman -S --needed {joined}"
    if fam == "suse":
        return f"sudo zypper install {joined}"
    return f"(install with your package manager: {joined})"


def install_hint(*logical: str) -> str:
    return install_command(packages(*logical))


def serial_group() -> str:
    """The group that owns serial ports: 'dialout' on most distros, 'uucp' on Arch."""
    import glob
    import grp
    for dev in glob.glob("/dev/ttyUSB*") + glob.glob("/dev/ttyACM*") + ["/dev/ttyS0"]:
        try:
            group = grp.getgrgid(os.stat(dev).st_gid).gr_name
        except (OSError, KeyError):
            continue
        if group not in ("root", "tty"):
            return group
    return "uucp" if family() == "arch" else "dialout"


def _plan(logical: list[str]) -> None:
    """For install.sh: family, missing packages, and the command to run."""
    need = missing(*logical)
    print(family())
    print(" ".join(need))
    print(install_command(need) if need else "")
    print("immutable" if immutable() else "mutable")


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "plan":
        _plan(sys.argv[2:])
    elif len(sys.argv) >= 2 and sys.argv[1] == "info":
        print(f"{name()} -> {family()}{' (immutable)' if immutable() else ''}")
    else:
        print(__doc__)
