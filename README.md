# Fröccs

*Fröccs* is the Hungarian wine spritzer: wine mixed with soda water. This
program is Wine with something mixed in to make it better.

Simple Windows-software installer for Linux. Proton under the hood (through
umu-launcher), tuned for programs and games.

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

Each program's ⋮ menu: Start (plus *Start …* for each extra program in it),
Settings, Install another program here, Run another .exe here, Paste a login
link, Open C: drive, Create restore point, Export, Force stop, Show log,
Remove. In grid view, right-click a tile.

### Several programs in one environment

Some programs need to see each other's files, for example a game and its mod
manager (CurseForge, Vortex…). Use ⋮ → **Install another program here…**, or
pick *Install into → Next to …* when adding software. The installer runs inside
the existing program's environment (same C: drive, registry and settings), a
restore point is made first, and the new programs you tick get their own
apps-menu entries. Command line: `froccs add <installer> --into <id>`.

### Keep a program on one screen

With more than one monitor: Settings → General → **Screen**. Windows open on the
chosen screen, and windows dragged off it are moved back. Fullscreen games go
fullscreen there. Each apps-menu entry can have its own screen (e.g. the game on
the big monitor, the mod manager on the other). Works on GNOME, KDE and X11
desktops. It doesn't work in native Wayland mode, because Wayland doesn't let
one program move another's windows. `froccs screens` lists the screen names.

### Games

Settings → Advanced → *Games*:

- **Real fullscreen** (on by default). Many games (World of Warcraft, most
  "windowed fullscreen"/borderless modes) don't ask for fullscreen. They open a
  frameless window as big as the screen, and the desktop keeps it below the top
  bar, so the game ends up one bar shorter than the screen. Fröccs spots such
  windows and makes them real fullscreen on their screen. While the game runs,
  Wine is told each screen is free up to its edges (the work area), so a game
  that maximizes itself after a resolution change covers the screen instead of
  ending up below the top bar with a "Custom" resolution. Turn it off for a
  program whose window goes fullscreen when it shouldn't.
- **Use my screen's resolution** (on by default). Games remember their
  resolution in their own settings file, and under Wine that saved value often
  goes wrong: 60 Hz on a 144 Hz screen, the wrong monitor, or both monitors
  side by side. Before each start Fröccs fixes a saved value that can't be
  right (a resolution bigger than the screen, a refresh rate the screen can't
  do) in World of Warcraft (`WTF/Config.wtf`), Unreal Engine games
  (`GameUserSettings.ini`) and Unity games (registry). A resolution or refresh
  rate you picked in the game that fits the screen is kept. Only resolution,
  refresh rate and, if you chose a *Screen*, the monitor are changed. A game
  that hasn't saved its settings yet gets them from its second start on.
  Changes are noted in the program's log.
- **Run in Gamescope**: the game runs inside Valve's Gamescope (as on the Steam
  Deck). It's always true fullscreen and the game sees exactly one screen. Try it
  when a game's window, resolution or mouse misbehaves. Needs the `gamescope`
  package. It now gets the screen's refresh rate (e.g. 144 Hz) instead of
  possibly running at 60 Hz.
- **GameMode**: now actually switches on. `gamemoderun` couldn't work inside
  Proton's Steam Runtime container, so Fröccs asks the GameMode service directly.
- **Proton version**: Valve's own builds that Steam installed (Proton 10.0,
  Proton - Experimental…) can be pinned next to GE-Proton. GE-Proton is Valve's
  Proton plus extra fixes, so it stays the default. Valve's build is worth a try
  when a game breaks on GE-Proton.
- Proton's Xalia gamepad helper is turned off (on a PC it only costs CPU).
  Put `PROTON_USE_XALIA=1` in the environment variables to get it back.

### Settings per program

| Page | What's there |
|---|---|
| General | name, **display scaling**, **screen** (multi-monitor), start at login, launch arguments and environment variables (with an on/off switch), apps-menu entries |
| Files | use my own Documents/Desktop/Downloads, hide drive Z:, extra drive letters, **file types** ("Open with"), **serial ports** COM1–COM4 for PLC cables |
| Advanced | Proton version (pin one, including Valve's builds from Steam), graphics (Vulkan/OpenGL), NTSYNC, native Wayland, DLL overrides, **games** (real fullscreen, screen resolution, Gamescope, GameMode, MangoHud, FPS limit), login links, debug mode |
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
- **Games:** GameMode, MangoHud overlay and an FPS limit per program (see *Games* above).

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
import | doctor | links | url | refresh | remove | screens` (see `froccs --help`).

Files live in `~/.local/share/froccs/` (prefixes, templates, snapshots,
logs). Recipes are JSON files in `froccs/recipes/`, easy to add to.

## License

Fröccs is free software: you can use, study, share and change it under the terms
of the [GNU General Public License v3.0](LICENSE). Changed versions you share must
stay under the same license.
