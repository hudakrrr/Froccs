#!/usr/bin/env bash
# Installs or updates Fröccs for the current user.
# Run it WITHOUT sudo:  ./install.sh   (asks for your password only to install packages)
# Works on Fedora (also Silverblue/Bazzite), Debian/Ubuntu/Mint/Pop!_OS,
# Arch/Manjaro/EndeavourOS/CachyOS and openSUSE.
# Updating from a version before 0.5 moves all your programs over.
set -euo pipefail
cd "$(dirname "$0")"

if [ "$(id -u)" -eq 0 ]; then
    echo "Please run this without sudo:  ./install.sh"
    echo "(With sudo, Fröccs would be installed for root instead of you.)"
    exit 1
fi

FROM_APP="${FROCCS_FROM_APP:-}"
LIB="$HOME/.local/lib/froccs"
BIN="$HOME/.local/bin"
APPS="$HOME/.local/share/applications"
ICONS="$HOME/.local/share/icons/hicolor"
APP_ID="io.github.froccs.Froccs"
mkdir -p "$LIB" "$BIN" "$APPS"

# --- 0. Older setup? Move programs and data first, remove leftovers ------
# Done before anything is installed: if a program is still running, nothing
# changes and the old version keeps working.
echo "==> Checking for an older setup"
PYTHONPATH="$PWD" /usr/bin/python3 -m froccs migrate

# --- 1. System packages (GTK, icons) -------------------------------------
# froccs.distro knows each distro's package manager and package names.
mapfile -t PLAN < <(PYTHONPATH="$PWD" /usr/bin/python3 -m froccs.distro plan gui icons desktop tools)
FAMILY="${PLAN[0]:-unknown}"; NEED="${PLAN[1]:-}"; CMD="${PLAN[2]:-}"; KIND="${PLAN[3]:-mutable}"
echo "==> System: $(PYTHONPATH="$PWD" /usr/bin/python3 -m froccs.distro info)"
if [ -n "$NEED" ]; then
    if [ -n "$FROM_APP" ]; then
        # Updating from inside the app: no terminal to type a password into.
        echo "NOTE: packages missing: $NEED ($CMD)"
    elif [ "$KIND" = "immutable" ]; then
        echo "==> Your system is read-only (Silverblue/Kinoite/Bazzite…). Missing: $NEED"
        echo "    Install them with:  $CMD"
        echo "    (or reboot after 'sudo rpm-ostree install $NEED'), then run ./install.sh again."
    elif [ "$FAMILY" = "unknown" ]; then
        echo "==> Unknown distribution. Make sure these are installed:"
        echo "    Python GObject (PyGObject), GTK 4, libadwaita 1.5+, icoutils,"
        echo "    desktop-file-utils, xdg-utils, shared-mime-info, curl, tar, zstd"
    else
        echo "==> Installing: $NEED"
        case "$FAMILY" in
            fedora) sudo dnf install -y $NEED ;;
            debian) sudo apt-get install -y $NEED ;;
            arch)   sudo pacman -S --needed --noconfirm $NEED ;;
            suse)   sudo zypper --non-interactive install $NEED ;;
        esac
    fi
fi

# The window needs GTK 4 and libadwaita 1.5 or newer (Ubuntu 24.04, Debian 13,
# Fedora 40, current Arch/openSUSE). Older systems can still use the command line.
if ! /usr/bin/python3 - <<'PYCHECK' 2>/dev/null
import gi
gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1")
from gi.repository import Adw
raise SystemExit(0 if (Adw.MAJOR_VERSION, Adw.MINOR_VERSION) >= (1, 5) else 1)
PYCHECK
then
    echo "WARNING: GTK 4 / libadwaita 1.5+ for Python not found, so the Fröccs window"
    echo "         won't open on this system. The 'froccs' command still works."
fi

# --- 2. umu-launcher (official GitHub build: same on every distro) -------
if command -v umu-run >/dev/null 2>&1 || [ -x "$BIN/umu-run" ]; then
    echo "==> umu-launcher already installed"
else
    echo "==> Downloading umu-launcher from GitHub"
    URL=$(curl -fsSL https://api.github.com/repos/Open-Wine-Components/umu-launcher/releases/latest 2>/dev/null |
          python3 -c 'import json,sys
try:
    for a in json.load(sys.stdin)["assets"]:
        if a["name"].endswith("zipapp.tar"):
            print(a["browser_download_url"]); break
except Exception:
    pass' || true)
    # Fallback if the GitHub API is unreachable or rate-limited
    URL=${URL:-https://github.com/Open-Wine-Components/umu-launcher/releases/download/1.4.4/umu-launcher-1.4.4-zipapp.tar}
    echo "    $URL"
    TMP=$(mktemp -d)
    trap 'rm -rf "$TMP"' EXIT
    curl -fL --progress-bar -o "$TMP/umu.tar" "$URL"
    tar -xf "$TMP/umu.tar" -C "$TMP"
    UMU=$(find "$TMP" -type f -name umu-run | head -1)
    [ -n "$UMU" ] || { echo "umu-run not found in the download."; exit 1; }
    install -m 755 "$UMU" "$BIN/umu-run"
    echo "    installed $BIN/umu-run"
fi

# --- 3. Fröccs itself ----------------------------------------------------
# Only the program code in $LIB is replaced. Your installed software lives in
# ~/.local/share/froccs and is never touched by an update.
NEW_VER=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' froccs/__init__.py)
OLD_VER=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$LIB/froccs/__init__.py" 2>/dev/null || true)
if [ -n "$OLD_VER" ]; then
    echo "==> Updating $OLD_VER -> Fröccs $NEW_VER (your programs are kept)"
else
    echo "==> Installing Fröccs $NEW_VER"
fi
rm -rf "$LIB/froccs.new"
cp -r froccs "$LIB/froccs.new"
rm -rf "$LIB/froccs"
mv "$LIB/froccs.new" "$LIB/froccs"

cat > "$BIN/froccs" <<EOF
#!/bin/sh
export PATH="$BIN:\$PATH"
PYTHONPATH="$LIB" exec /usr/bin/python3 -m froccs "\$@"
EOF
chmod +x "$BIN/froccs"

# App icon in every size the desktop may ask for
for f in data/icons/*.png; do
    size=$(basename "$f" .png)
    dir="$ICONS/${size}x${size}/apps"
    mkdir -p "$dir"
    cp "$f" "$dir/$APP_ID.png"
done
touch "$ICONS"
for c in gtk-update-icon-cache gtk4-update-icon-cache; do
    command -v "$c" >/dev/null && "$c" -f -t "$ICONS" >/dev/null 2>&1 && break
done
true

# File type for exported programs (double-click a .froccs file to import it)
mkdir -p "$HOME/.local/share/mime/packages"
cat > "$HOME/.local/share/mime/packages/froccs.xml" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<mime-info xmlns="http://www.freedesktop.org/standards/shared-mime-info">
  <mime-type type="application/x-froccs-export">
    <comment>Fröccs program export</comment>
    <glob pattern="*.froccs"/>
  </mime-type>
</mime-info>
EOF
update-mime-database "$HOME/.local/share/mime" >/dev/null 2>&1 || true

# MimeType = "Open with Fröccs" on Windows installers and export files
cat > "$APPS/$APP_ID.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Fröccs
Comment=Install and run Windows software
Exec=$BIN/froccs %f
Icon=$APP_ID
Categories=System;Utility;
StartupNotify=true
StartupWMClass=$APP_ID
MimeType=application/x-ms-dos-executable;application/x-msdownload;application/vnd.microsoft.portable-executable;application/x-msi;application/x-ms-installer;application/x-froccs-export;
EOF
xdg-mime default "$APP_ID.desktop" application/x-froccs-export 2>/dev/null || true
update-desktop-database "$APPS" 2>/dev/null || true

if [ -n "$OLD_VER" ] || [ -s "$HOME/.local/share/froccs/apps.json" ]; then
    echo "==> Refreshing the menu entries of your programs"
    "$BIN/froccs" refresh || true
fi

echo
echo "Done. Open 'Fröccs' from your apps menu, or run: froccs"
echo "The first install downloads GE-Proton and the Steam Runtime (~1 GB) once."
