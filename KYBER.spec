# KYBER.spec -- PyInstaller build spec (one-folder / --onedir).
#
#   Build:  pyinstaller KYBER.spec --noconfirm
#
# One-folder is deliberate: near-instant startup (no per-launch re-extraction),
# far fewer antivirus false-positives on an unsigned beta binary, and it avoids
# the one-file "different _MEIPASS per subprocess" trap that would otherwise
# break the file-based worker handshakes. The installer hides the folder so
# testers still just see "KYBER".
#
# NOTE: BLE (winrt/bleak) and faster-whisper (ctranslate2) are the two areas
# most likely to need an extra hidden import or data file after the first
# build -- if a frozen run fails with ModuleNotFoundError, add the named module
# to hiddenimports here and rebuild. That iteration is normal for these libs.

from PyInstaller.utils.hooks import (
    collect_submodules,
    collect_data_files,
    collect_dynamic_libs,
)

# --- read-only data bundled into the app (loaded via sys._MEIPASS) ---
datas = [
    ("sound_discovery_ui.html", "."),
    ("kyber_provision_page.html", "."),
    ("VERSION", "."),                          # single-source app version
    ("default_maps/*.json", "default_maps"),   # seed personalities on first run
]
datas += collect_data_files("faster_whisper")
datas += collect_data_files("sounddevice")
datas += collect_data_files("webview")          # pywebview runtime assets

# --- native libraries PyInstaller won't find on its own ---
binaries = []
binaries += collect_dynamic_libs("sounddevice")  # PortAudio DLL
binaries += collect_dynamic_libs("ctranslate2")  # whisper inference backend

# --- dynamically-imported modules PyInstaller's static analysis misses ---
hiddenimports = []
hiddenimports += collect_submodules("bleak")           # winrt backend is dynamic
hiddenimports += collect_submodules("winrt")
hiddenimports += collect_submodules("faster_whisper")
hiddenimports += collect_submodules("ctranslate2")
hiddenimports += collect_submodules("droiddepot")
hiddenimports += collect_submodules("dbeacon")
hiddenimports += collect_submodules("huggingface_hub")   # whisper model fetch
hiddenimports += [
    "pystray._win32",
    "PIL._tkinter_finder",
]

block_cipher = None

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="KYBER",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,            # tray app -- no console window.
                              # Flip to True for a debug build to see logs.
    disable_windowed_traceback=False,
    icon='kyber.ico',         # crystal mark -> window + taskbar icon once frozen
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="KYBER",
)
