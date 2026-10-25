"""
Look inside an installer before running it (like Bottles' "Eagle").

A Windows .exe is a "PE" file: a header, a table of sections, and data
directories. Entry 14 of the data directories is the CLR header, present only
in .NET programs. Installers are usually wrappers (Inno Setup, NSIS, WiX
Burn, InstallShield, MSI) with the real files compressed inside, so besides the
header we also search the raw bytes for tell-tale strings. That's a heuristic,
not proof, which is why the user can still flip the result in the dialog.
"""
from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field
from pathlib import Path

HEAD = 48 << 20     # bytes read from the start
TAIL = 16 << 20     # and from the end (payloads / manifests often sit there)


@dataclass
class Analysis:
    kind: str = "Program"             # installer technology, for display
    dotnet: bool = False              # needs .NET Framework
    dotnet_core: bool = False         # needs modern .NET (5+) desktop runtime
    frameworks: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    is_64bit: bool = False


def _read(path: Path) -> bytes:
    size = path.stat().st_size
    with open(path, "rb") as f:
        if size <= HEAD + TAIL:
            return f.read()
        head = f.read(HEAD)
        f.seek(size - TAIL)
        return head + f.read()


def _pe_info(data: bytes) -> tuple[bool, bool]:
    """(is .NET assembly, is 64-bit) from the PE header."""
    try:
        if data[:2] != b"MZ":
            return False, False
        pe = struct.unpack_from("<I", data, 0x3C)[0]
        if data[pe:pe + 4] != b"PE\0\0":
            return False, False
        machine = struct.unpack_from("<H", data, pe + 4)[0]
        opt = pe + 24
        magic = struct.unpack_from("<H", data, opt)[0]
        dd = opt + (96 if magic == 0x10B else 112)     # PE32 / PE32+
        clr_rva, clr_size = struct.unpack_from("<II", data, dd + 14 * 8)
        return clr_rva != 0 and clr_size != 0, machine in (0x8664, 0xAA64)
    except struct.error:
        return False, False


_KINDS = [
    (rb"Inno Setup", "Inno Setup installer"),
    (rb"Nullsoft", "NSIS installer"),
    (rb"\.wixburn|WixBundle|WixStdBA", "WiX Burn bundle"),
    (rb"InstallShield", "InstallShield installer"),
    (rb"7zS\.sfx|7-Zip SFX|;!@Install@!UTF-8!", "7-Zip self-extractor"),
    (rb"Squirrel|Update\.exe --install", "Squirrel (Electron) installer"),
]

_SIGNS = {
    "dotnet": rb"\.NETFramework,Version=v4|mscoree\.dll|NDP4\d+|dotNetFx4|"
              rb"NetFx4|_CorExeMain|System\.Windows\.Forms",
    "dotnet_core": rb"Microsoft\.WindowsDesktop\.App|hostfxr\.dll|"
                   rb"windowsdesktop-runtime|\.NETCoreApp,Version=v[5-9]",
    "WPF": rb"PresentationFramework",
    "Qt": rb"Qt[56]Core|Qt[56]Widgets",
    "Electron": rb"electron\.asar|Electron Framework",
    "Java": rb"jvm\.dll|javaw\.exe",
    "DirectX 12": rb"d3d12\.dll",
    "Visual C++": rb"vcruntime140|msvcp140",
}

_DRIVER_HINT = rb"dpinst|DIFxApp|WdfCoInstaller|\\drivers\\[A-Za-z0-9_]+\.sys|InstallHinfSection"
_SERVICE_HINT = rb"CreateServiceW|sc(?:\.exe)? create"


def analyze(path: Path) -> Analysis:
    a = Analysis()
    try:
        data = _read(path)
    except OSError:
        return a

    if data[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        a.kind = "Windows Installer (MSI)"
    else:
        for pat, name in _KINDS:
            if re.search(pat, data):
                a.kind = name
                break

    is_net, a.is_64bit = _pe_info(data)
    if is_net:
        a.dotnet = True
        a.frameworks.append(".NET")
    for key, pat in _SIGNS.items():
        if re.search(pat, data):
            if key == "dotnet":
                a.dotnet = True
                if ".NET" not in a.frameworks:
                    a.frameworks.append(".NET")
            elif key == "dotnet_core":
                a.dotnet_core = True
                a.frameworks.append(".NET (modern)")
            else:
                a.frameworks.append(key)
    if "WPF" in a.frameworks:
        a.dotnet = True

    if re.search(_DRIVER_HINT, data, re.I):
        a.warnings.append("Installs Windows hardware drivers. Those can't work under "
                          "Wine - devices that need them (e.g. USB PLC cables) won't "
                          "be usable. The rest of the program may still work.")
    if "DirectX 12" in a.frameworks:
        a.warnings.append("Uses DirectX 12: needs a Vulkan-capable GPU driver.")
    return a
