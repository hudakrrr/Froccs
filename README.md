# Fröccs

*Fröccs* is the Hungarian wine spritzer: wine mixed with soda water. This
program is Wine with something mixed in to make it better.

Simple Windows-software installer for Linux. Proton under the hood (through
umu-launcher), tuned for programs instead of games.

## Download

Get the latest `froccs-X.Y.Z.tar.gz` from the **[Releases page](../../releases/latest)**
(or from [komoj.hu/froccs](https://komoj.hu/froccs)), then follow *Install / update* below.

## Supported systems

| Distribution | Package manager | Notes |
|---|---|---|
| Fedora 40+ | dnf | |
| Fedora Silverblue / Kinoite, Bazzite | rpm-ostree | read-only system: install.sh prints the command |
| Ubuntu 24.04+, Linux Mint 22+, Pop!_OS, Debian 13+ | apt | |
| Arch, Manjaro, EndeavourOS, CachyOS | pacman | serial ports: group `uucp` instead of `dialout` |
| openSUSE Tumbleweed | zypper | |
| others | – | works if Python GObject, GTK 4 and libadwaita 1.5+ are installed |

The window needs GTK 4 and **libadwaita 1.5 or newer** (Ubuntu 22.04 and Debian
12 are too old for the window; the `froccs` command still works there).
Automatic scaling understands GNOME, KDE Plasma, other Wayland desktops (Sway,
Hyprland…) and X11 sessions (Cinnamon, Xfce, MATE…).

## Install / update

```bash
tar xzf froccs-<version>.tar.gz   # or use the in-app update banner
cd froccs
./install.sh                         # run WITHOUT sudo; updates keep your programs
```

## Use

- **Add software:** the + button, drag an installer onto the window, or
  right-click an .exe/.msi → *Open with Fröccs*.
- Fröccs **analyses the installer** (.NET? WPF? drivers?) and **recognises
  known software** (Fusion, AutoCAD/Inventor, CODESYS, CX-One, TIA Portal) to
  pick the right settings. You only choose the name and the scaling. Advanced
  lets you override the guess.
- After installing, tick which programs go in your apps menu. The first one is
  the main program; the others become extra entries (configuration tools etc.).

Each program's ⋮ menu: Start, Settings, Run another .exe here, Paste a login
link, Open C: drive, Create restore point, Export, Force stop, Show log,
Remove. In grid view, right-click a tile.

### Settings per program

| Page | What's there |
|---|---|
| General | name, **display scaling**, start at login, launch arguments and environment variables (with an on/off switch), apps-menu entries |
| Files | use my own Documents/Desktop/Downloads, hide drive Z:, extra drive letters, **file types** ("Open with"), **serial ports** COM1–COM4 for PLC cables |
| Advanced | Proton version (pin one), graphics (Vulkan/OpenGL), NTSYNC, native Wayland, DLL overrides, login links, debug mode |
| Backup | **restore points** (create, restore, delete), export to a `.froccs` file |

### Main menu

Import a `.froccs` file · import an existing Wine/Steam prefix (copied, the
original keeps working) · Proton versions (download newest GE-Proton, delete
unused ones) · Health check · Preferences (default scaling, background
preparation, automatic restore points, install-speed options, language).

## New in 0.4

- **Automatic scaling** (Settings → General → *Match my screen automatically*):
  at each start the program gets the scaling of your main screen, e.g. 125 % on
  the laptop and 100 % on an external monitor. It reads GNOME's screen setup over
  D-Bus. If GNOME stretches X11 apps itself (the default with fractional scaling),
  it picks 100 % so nothing gets scaled twice.
- **Updates from inside the app:** save a newer `froccs-X.Y.Z.tar.gz` to your
  Downloads or Documents folder and a banner offers to install it. Or use
  *Update Fröccs from a file…* in the menu. Your programs are kept.
- **Search** (start typing, or the magnifier button) and **Disk usage &
  clean-up**: sizes per program, its temp files and restore points, plus shared
  data (templates, Proton, runtime, download cache, logs), with clean buttons.
- **Fonts:** ⋮ → *Install fonts…* or Settings → Files → Fonts. Copies them to
  C:\Windows\Fonts and registers them like Windows does (CAD programs read that
  list).
- **Compatibility links** in the Add dialog: WineHQ AppDB and ProtonDB search for
  the program.
- **Printing** check in the health check (CUPS running, printers, PDF printer).
- **Games:** GameMode, MangoHud overlay and an FPS limit per program.

## Under the hood

- **Two templates** instead of per-type ones: *base* (fonts, VC++, MSXML,
  GDI+) and *dotnet* (base + .NET 4.8). They're built in the background so you
  don't wait for .NET when adding a program. A new app is an instant reflink
  copy on Btrfs.
- **Restore points** are reflink copies too: almost free until the program
  changes files. Automatic ones are made before running another .exe in an app
  and before changing its Proton version (last 3 kept).
- **Serial ports** are mapped through Wine's registry
  (`HKLM\Software\Wine\Ports`) and `dosdevices/comN`. Use the stable
  `/dev/serial/by-id/...` names. Your user must be in the `dialout` group;
  the health check tells you.
- **File types** and **login links** are read from the program's own Windows
  registry and mirrored into Linux (MIME types / `x-scheme-handler`). They're
  re-checked 90 s after each start, because programs often register them on
  first run or move them on update.
- **NTSYNC:** `PROTON_USE_NTSYNC=1` when `/dev/ntsync` exists.
- **Speed options (Preferences, experimental):** no-copy-on-write folders on
  Btrfs; *installer speed mode* preloads libeatmydata during installs so
  fsync calls become no-ops.

Command line: `froccs list | add | run | scale | stop | snapshot | export |
import | doctor | links | url | refresh | remove` (see `froccs --help`).

Files live in `~/.local/share/froccs/` (prefixes, templates, snapshots,
logs). Recipes are JSON files in `froccs/recipes/`, easy to add to.

## Being realistic

- **CODESYS:** works reasonably. The recipe reminds you which installer
  options to skip.
- **Fusion:** works with community fixes. Login goes through the browser
  (supported).
- **AutoCAD / Inventor:** hit-and-miss, especially newer versions.
- **TIA Portal / STEP 7:** needs Windows drivers and services, so use a Windows
  VM (WinBoat).
- **USB PLC cables** with vendor drivers don't work. USB-serial adapters and
  Ethernet do.

## License

Fröccs is free software: you can use, study, share and change it under the terms
of the [GNU General Public License v3.0](LICENSE). Changed versions you share must
stay under the same license.
