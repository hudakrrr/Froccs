"""
Translations. _("English text") returns the text in the user's language.

The language comes from Preferences (Auto / English / Magyar); "Auto" follows
the system (LANG=hu_HU.UTF-8 -> Hungarian). A missing translation simply
shows the English text. To add a language: add a dict like HU and list it in
LANGUAGES.
"""
from __future__ import annotations

import json
import locale
import os

from .paths import SETTINGS_FILE

HU = {
    # --- install progress
    "Running the installer\nFollow the installer's own window. If it starts the program at the end, close the program to continue.":
        "A telepítő fut\nKövesd a telepítő saját ablakát. Ha a végén elindítja a programot, zárd be a programot a folytatáshoz.",
    "Preparing a private copy for this app": "Saját példány készítése ehhez a programhoz",
    "Looking for the installed program": "A telepített program keresése",
    "Applying program-specific settings": "Programspecifikus beállítások alkalmazása",
    "Preparing the .NET environment": "A .NET-környezet előkészítése",
    "Creating the Windows environment\n(the first time this downloads Proton, a few minutes)":
        "A Windows-környezet létrehozása\n(első alkalommal letölti a Protont, ez pár perc)",
    "Setting display scaling to {pct} %": "Megjelenítési méretezés beállítása: {pct} %",
    "{path} already exists": "{path} már létezik",
    "Installing {what}": "Telepítés: {what}",
    "Waiting for the background preparation to finish…": "Várakozás a háttérben futó előkészítésre…",
    "Could not create the Windows environment.\nSee {log}": "Nem sikerült létrehozni a Windows-környezetet.\nRészletek: {log}",
    # --- components
    "Microsoft core fonts": "Microsoft alapbetűtípusok",
    "Visual C++ runtime": "Visual C++ futtatókörnyezet",
    ".NET Framework 4.8 (slow, about 10 minutes)": ".NET Framework 4.8 (lassú, kb. 10 perc)",
    "MSXML 6": "MSXML 6",
    "GDI+": "GDI+",
    "MFC runtime": "MFC futtatókörnyezet",
    "font smoothing": "betűsimítás",
    "Arial font": "Arial betűtípus",
    "Visual C++ 2005 runtime": "Visual C++ 2005 futtatókörnyezet",
    "Visual C++ 2008 runtime": "Visual C++ 2008 futtatókörnyezet",
    "Visual C++ 2013 runtime": "Visual C++ 2013 futtatókörnyezet",
    # --- graphics / DLL modes
    "Vulkan (DXVK) - fastest, for 3D/CAD": "Vulkan (DXVK) – leggyorsabb, 3D/CAD-hez",
    "OpenGL (WineD3D) - most compatible for 2D tools": "OpenGL (WineD3D) – legkompatibilisebb 2D-s programokhoz",
    "native,builtin": "Windows-os, majd Wine-os",
    "builtin,native": "Wine-os, majd Windows-os",
    "native": "csak Windows-os",
    "builtin": "csak Wine-os",
    "disabled": "kikapcsolva",
    # --- installer analysis
    "Program": "Program",
    "Windows Installer (MSI)": "Windows Installer (MSI)",
    "Inno Setup installer": "Inno Setup telepítő",
    "NSIS installer": "NSIS telepítő",
    "WiX Burn bundle": "WiX Burn csomag",
    "InstallShield installer": "InstallShield telepítő",
    "7-Zip self-extractor": "7-Zip önkicsomagoló",
    "Squirrel (Electron) installer": "Squirrel (Electron) telepítő",
    "Installs Windows hardware drivers. Those can't work under Wine - devices that need them (e.g. USB PLC cables) won't be usable. The rest of the program may still work.":
        "Windows-os hardverillesztőket telepít. Ezek Wine alatt nem működnek, így az ezeket igénylő eszközök (pl. USB-s PLC-kábelek) nem lesznek használhatók. A program többi része ettől még működhet.",
    "Uses DirectX 12: needs a Vulkan-capable GPU driver.": "DirectX 12-t használ: Vulkan-képes videokártya-illesztő kell hozzá.",
    # --- recipes
    "Sign-in happens in your browser. When the browser asks to open the link, allow it. If login still doesn't reach Fusion, try adding FUSION_IDSDK=false under Settings → Launch options → Environment variables.":
        "A bejelentkezés a böngészőben történik. Amikor a böngésző rákérdez a hivatkozás megnyitására, engedélyezd. Ha a bejelentkezés így sem jut el a Fusionig, próbáld meg hozzáadni a FUSION_IDSDK=false értéket: Beállítások → Indítási beállítások → Környezeti változók.",
    "Recent AutoCAD, Inventor and Revit versions often fail to install or start under Wine. It's worth a try, but plan for a Windows VM (WinBoat) if it doesn't work.":
        "Az újabb AutoCAD, Inventor és Revit verziók Wine alatt gyakran nem települnek vagy nem indulnak el. Érdemes megpróbálni, de ha nem megy, számolj Windows-os virtuális géppel (WinBoat).",
    "In the CODESYS installer choose a custom setup and keep only CODESYS itself: deselect Gateway, OPC and Control services. Don't install the SVN plug-in, it breaks the installation.":
        "A CODESYS telepítőben válaszd az egyéni telepítést, és csak magát a CODESYS-t hagyd bejelölve: a Gateway, az OPC és a Control szolgáltatásokat vedd ki. Az SVN bővítményt ne telepítsd, mert tönkreteszi a telepítést.",
    "TIA Portal and STEP 7 need Windows services and drivers that Wine doesn't have, so they almost certainly won't work here. Use a Windows VM (WinBoat) for them.":
        "A TIA Portalhoz és a STEP 7-hez olyan Windows-szolgáltatások és illesztők kellenek, amelyek a Wine-ban nincsenek meg, így itt szinte biztosan nem fognak működni. Ezekhez használj Windows-os virtuális gépet (WinBoat).",
    "Omron's USB drivers can't work under Wine. Connect to the PLC over a USB-serial adapter (map it under Settings → Files → Serial ports) or over Ethernet instead.":
        "Az Omron USB-illesztői Wine alatt nem működnek. Csatlakozz a PLC-hez USB–soros átalakítóval (Beállítások → Fájlok → Soros portok) vagy Etherneten.",
    # --- health check
    "umu-launcher": "umu-launcher",
    "Missing: nothing can run without it.": "Hiányzik: nélküle semmi sem tud futni.",
    "Run install.sh again (without sudo)": "Futtasd újra az install.sh-t (sudo nélkül)",
    "Proton": "Proton",
    "None downloaded yet. The first install downloads GE-Proton automatically (~500 MB).":
        "Még nincs letöltve. Az első telepítés automatikusan letölti a GE-Protont (~500 MB).",
    "Vulkan graphics driver": "Vulkan grafikus illesztő",
    "Not found. DXVK graphics need Vulkan.": "Nem található. A DXVK grafikához Vulkan kell.",
    "Available. Can be turned on per app.": "Elérhető. Programonként bekapcsolható.",
    "Exists but your user can't open /dev/ntsync.": "Létezik, de a felhasználód nem tudja megnyitni a /dev/ntsync-et.",
    "Kernel module not loaded (optional, makes heavy programs faster).":
        "A kernelmodul nincs betöltve (nem kötelező, a nehéz programokat gyorsítja).",
    "Program icons": "Programikonok",
    "icoutils installed": "Az icoutils telepítve van",
    "icoutils missing: shortcuts get a generic icon.": "Hiányzik az icoutils: a parancsikonok általános ikont kapnak.",
    "Power mode": "Energiagazdálkodási mód",
    "Serial ports (PLC cables)": "Soros portok (PLC-kábelek)",
    "Your user isn't in the '{group}' group, so programs can't open USB-serial adapters. Log out and in after the fix.":
        "A felhasználód nincs a „{group}” csoportban, így a programok nem tudják megnyitni az USB–soros átalakítókat. A javítás után jelentkezz ki és be.",
    "System": "Rendszer",
    "read-only system": "csak olvasható rendszer",
    "unknown distribution: package hints may not fit": "ismeretlen disztribúció: a csomagjavaslatok nem biztos, hogy illenek",
    "File system": "Fájlrendszer",
    "Free disk space": "Szabad lemezterület",
    "Installer speed mode": "Gyors telepítési mód",
    "On, but libeatmydata is missing, so it does nothing.": "Be van kapcsolva, de hiányzik a libeatmydata, így nem csinál semmit.",
    "Your browser won't send login links to this program. Open its Settings → Advanced → Scan again.":
        "A böngésződ nem fogja ennek a programnak küldeni a bejelentkezési hivatkozásokat. Nyisd meg a Beállítások → Speciális → Újrakeresés lehetőséget.",
    # --- restore points / backup
    "Manual": "Kézi",
    "Before running another program": "Másik program futtatása előtt",
    "Before changing the Proton version": "A Proton-verzió váltása előtt",
    "Close the program first: Windows keeps its settings in memory while it runs, so a copy now would be incomplete.":
        "Előbb zárd be a programot: futás közben a Windows a memóriában tartja a beállításait, így a másolat most hiányos lenne.",
    "Close the program before exporting it.": "Exportálás előtt zárd be a programot.",
    "That folder doesn't look like a Wine prefix (no drive_c and system.reg inside).":
        "Ez a mappa nem tűnik Wine-prefixnek (nincs benne drive_c és system.reg).",
    # --- window
    "Display scaling": "Megjelenítési méretezés",
    "100 % = normal. Use 125 % or 150 % on laptop screens.": "100 % = normál. Laptopkijelzőn használj 125 %-ot vagy 150 %-ot.",
    "Heads-up": "Figyelem",
    "Checking the installer…": "A telepítő ellenőrzése…",
    "nothing special detected": "semmi különleges",
    "Some installers leave background services running, so Fröccs can't tell when they are done. Click this once the installer window is closed.":
        "Egyes telepítők a háttérben futó szolgáltatásokat hagynak maguk után, így a Fröccs nem tudja megállapítani, mikor végeztek. Kattints ide, ha a telepítő ablaka bezárult.",
    "OK": "OK",
    "Choose the program": "Válaszd ki a programot",
    "Choose a folder": "Válassz mappát",
    "Login & web links": "Bejelentkezés és webes hivatkozások",
    "Logs": "Naplók",
    "None found. If the program logs in through your browser, start it once, then click Scan again.":
        "Nem található. Ha a program a böngészőn keresztül jelentkeztet be, indítsd el egyszer, majd kattints az Újrakeresés gombra.",
    "Export": "Exportálás",
    "Save this program with all its settings as one file. Import it in Fröccs on the other computer.":
        "A program mentése minden beállításával együtt egyetlen fájlba. A másik gépen importáld a Fröccsben.",
    "Cancel": "Mégse",
    "Create": "Létrehozás",
    "Restore": "Visszaállítás",
    "Download the newest GE-Proton": "A legújabb GE-Proton letöltése",
    "About 500 MB": "Kb. 500 MB",
    "Checking…": "Ellenőrzés…",
    "Default display scaling": "Alapértelmezett méretezés",
    "Import a Fröccs file…": "Fröccs-fájl importálása…",
    "Import an existing Wine or Steam folder…": "Meglévő Wine- vagy Steam-mappa importálása…",
    "Proton versions": "Proton-verziók",
    "Health check": "Állapotellenőrzés",
    "Preferences": "Beállítások",
    "About Fröccs": "A Fröccs névjegye",
    "Choose a Windows installer": "Válassz Windows-telepítőt",
    "Choose a Fröccs file": "Válassz Fröccs-fájlt",
    "Importing… this can take a few minutes": "Importálás… ez eltarthat néhány percig",
    "Choose the Wine prefix folder": "Válaszd ki a Wine-prefix mappáját",
    "Run a program in this app's environment": "Program futtatása ennek az alkalmazásnak a környezetében",
    "Log in": "Bejelentkezés",
    "Remove": "Eltávolítás",
    "Add software": "Szoftver hozzáadása",
    "Import": "Importálás",
    "Name in the apps menu": "Név az alkalmazásmenüben",
    "Advanced": "Speciális",
    "Set automatically. Change only if needed.": "Automatikusan beállítva. Csak szükség esetén változtasd.",
    ".NET Framework 4.8": ".NET Framework 4.8",
    "Needed by many engineering and business programs.": "Sok mérnöki és üzleti programnak kell.",
    "Graphics": "Grafika",
    "Install": "Telepítés",
    "The installer is finished": "A telepítő végzett",
    "Which programs go in your apps menu?": "Mely programok kerüljenek az alkalmazásmenübe?",
    "The first ticked one is the main program.": "Az első bejelölt lesz a fő program.",
    "Browse…": "Tallózás…",
    "Add to apps menu": "Hozzáadás a menühöz",
    "Installation failed": "A telepítés sikertelen",
    "No new program was found. Use “Browse…” to pick it yourself.": "Nem található új program. A „Tallózás…” gombbal kiválaszthatod magad.",
    "Choose which programs go in your apps menu.": "Válaszd ki, mely programok kerüljenek az alkalmazásmenübe.",
    "General": "Általános",
    "Name": "Név",
    "Start when I log in": "Indítás bejelentkezéskor",
    "Useful for licence servers and gateways.": "Licencszerverekhez és gatewayekhez hasznos.",
    "Launch options": "Indítási beállítások",
    "Use launch options": "Indítási beállítások használata",
    "Arguments": "Argumentumok",
    "Environment variables (NAME=value …)": "Környezeti változók (NÉV=érték …)",
    "Apps menu entries": "Menübejegyzések",
    "Extra programs from this app, e.g. configuration tools.": "Az alkalmazás további programjai, pl. konfigurációs eszközök.",
    "Add an entry": "Bejegyzés hozzáadása",
    "Change the main program": "A fő program módosítása",
    "Files": "Fájlok",
    "Folders": "Mappák",
    "Use my own folders": "A saját mappáim használata",
    "The program's Documents, Desktop, Downloads… are your real folders. Off: the program gets private ones.":
        "A program Dokumentumok, Asztal, Letöltések… mappái a te valódi mappáid. Kikapcsolva a program saját, külön mappákat kap.",
    "Hide the rest of my system": "A rendszer többi részének elrejtése",
    "Removes drive Z: (your whole Linux system). Your home folder is still reachable as H:.":
        "Eltávolítja a Z: meghajtót (a teljes Linux-rendszered). A saját mappád H:-ként továbbra is elérhető.",
    "Drives": "Meghajtók",
    "Show a folder to the program as a drive letter.": "Egy mappa megjelenítése a program számára meghajtóbetűjelként.",
    "Add a drive": "Meghajtó hozzáadása",
    "File types": "Fájltípusok",
    "Add a file type, e.g. dwg": "Fájltípus hozzáadása, pl. dwg",
    "Serial ports": "Soros portok",
    "Connect PLC cables (USB-serial adapters) to COM ports.": "PLC-kábelek (USB–soros átalakítók) hozzárendelése COM portokhoz.",
    "Not connected": "Nincs hozzárendelve",
    "Compatibility": "Kompatibilitás",
    "Proton version": "Proton-verzió",
    "Pin a version so updates can't break a working program.": "Rögzíts egy verziót, hogy a frissítések ne tehessenek tönkre egy működő programot.",
    "NTSYNC": "NTSYNC",
    "Native Wayland (experimental)": "Natív Wayland (kísérleti)",
    "Sharper on scaled screens, but some programs break.": "Méretezett kijelzőn élesebb, de egyes programok hibásan működnek vele.",
    "DLL overrides": "DLL-felülbírálások",
    "Choose whether Windows' own (native) or Wine's (built-in) version of a DLL is used. Many program fixes are exactly this.":
        "Megadhatod, hogy egy DLL-ből a Windows saját (native) vagy a Wine beépített (builtin) változata legyen használva. Sok programjavítás pontosan ennyi.",
    "Add a DLL, e.g. msvcp140": "DLL hozzáadása, pl. msvcp140",
    "Troubleshooting": "Hibaelhárítás",
    "Scan again": "Újrakeresés",
    "Debug mode": "Hibakereső mód",
    "Writes a detailed log of what Wine does. Slower; turn off when done.": "Részletes naplót ír arról, mit csinál a Wine. Lassabb; ha végeztél, kapcsold ki.",
    "Open": "Megnyitás",
    "Your browser sends these links to this program: {list}": "A böngésződ ezeket a hivatkozásokat küldi ennek a programnak: {list}",
    "Backup": "Mentés",
    "Restore points": "Visszaállítási pontok",
    "A saved copy of the program and its settings. Make one before updates or experiments; go back if something breaks.":
        "A program és beállításainak mentett másolata. Frissítés vagy kísérletezés előtt készíts egyet, és ha valami elromlik, visszatérhetsz rá.",
    "Move to another computer": "Áthelyezés másik gépre",
    "Export…": "Exportálás…",
    "No restore points yet": "Még nincs visszaállítási pont",
    "Before changes": "Módosítások előtt",
    "Create a restore point": "Visszaállítási pont létrehozása",
    "Go back to this restore point?": "Visszatérsz erre a visszaállítási pontra?",
    "The program is closed and everything since then is undone. Files you saved in your own folders are kept.":
        "A program bezárul, és minden azóta történt változás elvész. A saját mappáidba mentett fájlok megmaradnak.",
    "Programs set to “Newest” use the newest version here. Pinned programs keep theirs. Versions in use can't be deleted.":
        "A „Legújabb” beállítású programok az itteni legújabb verziót használják, a rögzítettek megtartják a sajátjukat. Használatban lévő verzió nem törölhető.",
    "Download": "Letöltés",
    "Not used": "Nincs használatban",
    "None yet": "Még nincs",
    "The first install downloads one automatically.": "Az első telepítés automatikusan letölt egyet.",
    "New programs": "Új programok",
    "Prepare Windows environments in the background": "Windows-környezetek előkészítése a háttérben",
    "The one-time .NET setup (~15 min) happens before you need it.": "Az egyszeri .NET-telepítés (~15 perc) még azelőtt lezajlik, hogy szükséged lenne rá.",
    "Safety": "Biztonság",
    "Automatic restore points": "Automatikus visszaállítási pontok",
    "Before running another program in an app (installers, updates) and before changing its Proton version. The last 3 are kept.":
        "Másik program (telepítő, frissítés) futtatása előtt egy alkalmazásban, illetve a Proton-verzió váltása előtt. Az utolsó 3 marad meg.",
    "Install speed (experimental)": "Telepítési sebesség (kísérleti)",
    "Faster writes on Btrfs": "Gyorsabb írás Btrfs-en",
    "Turns off copy-on-write for new programs. Uses more disk space; copies are no longer instant.":
        "Az új programoknál kikapcsolja a copy-on-write-ot. Több helyet foglal, és a másolás már nem azonnali.",
    "Skips the constant “save to disk now” calls during installs. Much faster, but a crash mid-install can damage that install. Needs libeatmydata.":
        "Telepítés közben kihagyja az állandó „azonnal lemezre írás” hívásokat. Sokkal gyorsabb, de egy telepítés közbeni összeomlás tönkreteheti azt a telepítést. A libeatmydata kell hozzá.",
    "Grid view": "Rácsnézet",
    "No software yet": "Még nincs szoftver",
    "Pick a Windows installer (.exe or .msi), or drop it here.\nFröccs installs it and puts it in your apps menu.":
        "Válassz egy Windows-telepítőt (.exe vagy .msi), vagy húzd ide.\nA Fröccs telepíti, és felveszi az alkalmazásmenübe.",
    "umu-launcher is missing. Run install.sh again (without sudo).": "Hiányzik az umu-launcher. Futtasd újra az install.sh-t (sudo nélkül).",
    "Start": "Indítás",
    "Settings": "Beállítások",
    "Run another .exe here…": "Másik .exe futtatása itt…",
    "Paste a login link…": "Bejelentkezési hivatkozás beillesztése…",
    "Open C: drive": "C: meghajtó megnyitása",
    "Create restore point": "Visszaállítási pont létrehozása",
    "Force stop": "Kényszerített leállítás",
    "Show log": "Napló megtekintése",
    "Install umu-launcher first (see the bar at the top)": "Előbb telepítsd az umu-launchert (lásd a felső sávot)",
    "Imported program": "Importált program",
    "Exporting… this can take a few minutes": "Exportálás… ez eltarthat néhány percig",
    "Paste the login link": "Illeszd be a bejelentkezési hivatkozást",
    "Creating restore point…": "Visszaállítási pont létrehozása…",
    "This deletes the program, its settings, its restore points and its C: drive. Files in your own folders are kept.":
        "Ez törli a programot, a beállításait, a visszaállítási pontjait és a C: meghajtóját. A saját mappáidban lévő fájlok megmaradnak.",
    "Known-good settings are applied automatically.": "A bevált beállítások automatikusan érvénybe lépnek.",
    "This program uses modern .NET. Most installers bring it along; if the program says it's missing, install the .NET Desktop Runtime from Microsoft with “Run another .exe here”.":
        "Ez a program modern .NET-et használ. A legtöbb telepítő magával hozza; ha a program hiányolja, telepítsd a Microsoft .NET Desktop Runtime-ot a „Másik .exe futtatása itt” menüponttal.",
    "{name} is now in your apps menu": "A(z) {name} bekerült az alkalmazásmenübe",
    "main": "fő",
    "No adapter is plugged in right now.": "Jelenleg nincs csatlakoztatva átalakító.",
    "Faster for heavy, multi-threaded programs.": "Gyorsabb a nehéz, többszálú programoknál.",
    "Not available: see the health check.": "Nem elérhető: lásd az állapotellenőrzést.",
    "Delete": "Törlés",
    "Restored": "Visszaállítva",
    "You already have the newest version.": "Már a legújabb verzió van meg.",
    "Menu": "Menü",
    "Tip: install icoutils to get program icons ({cmd})": "Tipp: telepítsd az icoutils csomagot a programikonokhoz ({cmd})",
    "Click to start, right-click for more": "Kattintás: indítás, jobb klikk: további lehetőségek",
    "That's not a Windows program (.exe or .msi)": "Ez nem Windows-program (.exe vagy .msi)",
    "Login link sent": "Bejelentkezési hivatkozás elküldve",
    "No log yet": "Még nincs napló",
    "Installer: {name}": "Telepítő: {name}",
    "Your screen runs at {pct} %. 100 % = normal size.": "A kijelződ {pct} %-on fut. 100 % = normál méret.",
    "Recognized: {name}": "Felismerve: {name}",
    "Uses: {list}": "Használja: {list}",
    "{name} is installed": "A(z) {name} telepítve",
    "These files get “Open with {name}” in your file manager.": "Ezeknél a fájloknál a fájlkezelőben megjelenik a „Megnyitás ezzel: {name}” lehetőség.",
    "Newest (currently {name})": "Legújabb (jelenleg {name})",
    "Scaling saved. Restart the program to see it.": "Méretezés mentve. Indítsd újra a programot, hogy lásd.",
    "Used by: {list}": "Használja: {list}",
    "Newest": "Legújabb",
    "Preparing Windows environments in the background:": "Windows-környezetek előkészítése a háttérben:",
    "Export {name}": "{name} exportálása",
    "Starting {name}…": "{name} indítása…",
    "After signing in, the browser tries to open a link like “adskidmgr:/login?code=…”. If nothing happened, copy that link (from the browser's console, F12) and paste it here. {name} must be running.":
        "Bejelentkezés után a böngésző egy „adskidmgr:/login?code=…” formájú hivatkozást próbál megnyitni. Ha nem történt semmi, másold ki ezt a hivatkozást (a böngésző konzoljából, F12) és illeszd be ide. A(z) {name} programnak futnia kell.",
    "Restore point created": "Visszaállítási pont létrehozva",
    "Remove {name}?": "Eltávolítod: {name}?",
    "Run Windows software on Linux with Proton.": "Windows-szoftverek futtatása Linuxon, Protonnal.",
    "Some components failed to install: {names}. The program may still work.": "Néhány összetevőt nem sikerült telepíteni: {names}. A program ettől még működhet.",
    "automatic": "automatikus",
    "Downloaded {name}": "Letöltve: {name}",
    "Restore point made, starting the program…": "Visszaállítási pont kész, a program indul…",
    "Starting the program…": "A program indul…",
    "{name} stopped": "A(z) {name} leállítva",
    "{name} isn't running": "A(z) {name} nem fut",
    "Exported to {path}": "Exportálva ide: {path}",
    "Command copied. Paste it in a terminal.": "Parancs kimásolva. Illeszd be egy terminálba.",
    "{name} removed": "A(z) {name} eltávolítva",
    "{name}: display scaling": "{name}: megjelenítési méretezés",
    "Set to {pct} % in Fröccs, but not applied inside the program's Windows environment.":
        "A Fröccsben {pct} % van beállítva, de a program Windows-környezetében nem lépett érvénybe.",
    "Tray icon window": "Tálcaikon-ablak",
    "GNOME has no tray, so Wine shows the program's tray icons in a small window of its own. Turn on if the program hides in the tray and you need the icon to get it back.":
        "A GNOME-ban nincs tálcaterület, ezért a Wine egy saját kis ablakban mutatja a program tálcaikonjait. Kapcsold be, ha a program a tálcára rejtőzik, és az ikonnal tudod csak visszahozni.",
    "Experiences of other users": "Mások tapasztalatai",
    "FPS limit": "FPS-korlát",
    "0 = no limit. Saves battery and keeps the laptop cooler.": "0 = nincs korlát. Kíméli az akkumulátort, és hűvösebben tartja a laptopot.",
    "Measuring…": "Mérés…",
    "Downloaded components": "Letöltött összetevők",
    "Update Fröccs from a file…": "Fröccs frissítése fájlból…",
    "Disk usage & clean-up": "Lemezhasználat és takarítás",
    "Choose a Fröccs release file": "Válassz Fröccs-kiadásfájlt",
    "Updating…": "Frissítés…",
    "Match my screen automatically": "Automatikus igazítás a kijelzőhöz",
    "Fonts": "Betűtípusok",
    "Extra fonts for this program, e.g. ISO or company fonts used in drawings. Close the program before changing them.":
        "További betűtípusok ehhez a programhoz, pl. rajzokon használt ISO- vagy céges betűtípusok. Módosításuk előtt zárd be a programot.",
    "Install fonts…": "Betűtípusok telepítése…",
    "Games": "Játékok",
    "GameMode": "GameMode",
    "MangoHud overlay": "MangoHud-átfedés",
    "On Btrfs, copies share data, so the real total is often lower than the sum.":
        "Btrfs-en a másolatok közösen használják az adatokat, ezért a valós összeg gyakran kisebb az itt látható értékek összegénél.",
    "Temporary files": "Ideiglenes fájlok",
    "Shared": "Közös",
    "Ready-made Windows environments": "Előkészített Windows-környezetek",
    "Steam Runtime": "Steam Runtime",
    "only needed to rebuild the environments": "csak a környezetek újraépítéséhez kell",
    "Clean": "Takarítás",
    "Search": "Keresés",
    "Search your programs": "Keresés a programjaid között",
    "Update": "Frissítés",
    "Later": "Később",
    "Restart now": "Újraindítás most",
    "See how well {name} runs for others before installing.": "Nézd meg telepítés előtt, mennyire jól fut a(z) {name} másoknál.",
    "Not available: couldn't read the screen setup from your desktop.": "Nem elérhető: nem sikerült kiolvasni a kijelzőbeállítást az asztali környezetből.",
    "Switches the computer to full performance while the program runs.": "A program futása alatt teljes teljesítményre kapcsolja a gépet.",
    "Not installed: {cmd}": "Nincs telepítve: {cmd}",
    "Shows FPS, CPU and GPU load on top of the program.": "Megjeleníti az FPS-t, valamint a CPU- és GPU-terhelést a program felett.",
    "Delete automatic ones": "Automatikusak törlése",
    "rebuilt automatically when needed": "szükség esetén automatikusan újraépül",
    "delete unused ones under Proton versions": "a nem használtakat a Proton-verzióknál törölheted",
    "Fröccs {version} is ready to install (found in {folder})": "A Fröccs {version} telepíthető (itt található: {folder})",
    "That's not a Fröccs release file": "Ez nem Fröccs-kiadásfájl",
    "Updated": "Frissítve",
    "Choose fonts for {name}": "Betűtípusok kiválasztása: {name}",
    "Update failed": "A frissítés sikertelen",
    "Restart Fröccs to use the new version. Your programs were kept.": "Indítsd újra a Fröccsöt az új verzió használatához. A programjaid megmaradtak.",
    "At each start, use the scaling of the screen. Right now: {pct} %": "Minden indításkor a kijelző méretezését használja. Most: {pct} %",
    "auto": "auto",
    "Installed {n} font(s)": "{n} betűtípus telepítve",
    "Cleaned up": "Kitakarítva",
    "Printing": "Nyomtatás",
    "CUPS tools not found, so printing from programs won't work.": "A CUPS eszközei nem találhatók, így a programokból nem lehet nyomtatni.",
    "Game helpers": "Játéksegédek",
    "GameMode and MangoHud are installed": "A GameMode és a MangoHud telepítve van",
    "The print service (CUPS) isn't running.": "A nyomtatási szolgáltatás (CUPS) nem fut.",
    "No printers set up. A PDF printer lets programs “print” to PDF files.": "Nincs beállított nyomtató. Egy PDF-nyomtatóval a programok PDF-fájlba „nyomtathatnak”.",
    "Optional, for games: {list} not installed.": "Nem kötelező, játékokhoz: {list} nincs telepítve.",
    "Printers programs can use: {list}": "A programok által használható nyomtatók: {list}",
    "No PDF printer: add one to “print” drawings to PDF.": "Nincs PDF-nyomtató: adj hozzá egyet, hogy a rajzokat PDF-be „nyomtathasd”.",
    "Close the program first.": "Előbb zárd be a programot.",
    "Why: Fröccs builds two ready-made Windows environments once, one basic and one with .NET 4.8. Every program you add starts from an instant copy of one of them, so you don't wait about 15 minutes for .NET in the middle of an install. You can keep using your programs meanwhile.":
        "Miért: a Fröccs egyszer felépít két előkészített Windows-környezetet, egy alapot és egy .NET 4.8-asat. Minden új program ezek egyikének azonnali másolatából indul, így telepítés közben nem kell kb. 15 percet várnod a .NET-re. Közben nyugodtan használhatod a programjaidat.",
    "Preparing…": "Előkészítés…",
    "Preparing Windows environments": "Windows-környezetek előkészítése",
    "Preparing… {pct} %": "Előkészítés… {pct} %",
    "Preparing Windows environments ({pct} %)": "Windows-környezetek előkészítése ({pct} %)",
    "Now: {step}": "Most: {step}",
    "Starting…": "Indítás…",
    "Done": "Kész",
    "Windows environments are ready: new programs install faster now": "A Windows-környezetek elkészültek: az új programok mostantól gyorsabban települnek",
    "Background preparation stopped: {msg}": "A háttérben futó előkészítés leállt: {msg}",
    "Login link received, passing it to the program…": "Bejelentkezési hivatkozás érkezett, továbbítás a programnak…",
    "Language": "Nyelv",
    "Automatic (system language)": "Automatikus (rendszernyelv)",
    "Takes effect after restarting Fröccs.": "A Fröccs újraindítása után lép érvénybe.",
}

LANGUAGES = {"hu": HU}


def _pick_language() -> str:
    try:
        lang = json.loads(SETTINGS_FILE.read_text()).get("language", "auto")
    except (OSError, ValueError):
        lang = "auto"
    if lang != "auto":
        return lang
    for var in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
        val = os.environ.get(var)
        if val:
            return val.split(":")[0][:2].lower()
    loc = locale.getlocale()[0] or ""
    return loc[:2].lower()


_TABLE = LANGUAGES.get(_pick_language(), {})

# gettext language codes for GTK/libadwaita's own texts (buttons, About...)
_TOOLKIT = {"en": "en_US:en", "hu": "hu_HU:hu"}
SYSTEM_LANGUAGE_VAR = "FROCCS_SYSTEM_LANGUAGE"


def apply_toolkit_language() -> None:
    """
    GTK and libadwaita translate their built-in texts (Cancel/Open buttons,
    the About window, search fields...) with gettext, which follows the
    LANGUAGE environment variable. When the user picked a language in
    Preferences, set it BEFORE GTK starts so those texts match. The system
    value is remembered so the Windows programs we launch keep their own
    language (see core.base_env).
    """
    try:
        choice = json.loads(SETTINGS_FILE.read_text()).get("language", "auto")
    except (OSError, ValueError):
        choice = "auto"
    if choice in _TOOLKIT and SYSTEM_LANGUAGE_VAR not in os.environ:
        os.environ[SYSTEM_LANGUAGE_VAR] = os.environ.get("LANGUAGE", "")
        os.environ["LANGUAGE"] = _TOOLKIT[choice]


def _(text: str) -> str:
    return _TABLE.get(text, text)
