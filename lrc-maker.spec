# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for LRC Maker -- standalone Windows executable.

Build with:
    pyinstaller lrc-maker.spec

Or use the convenience script:
    python build_release.py
"""

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules, collect_data_files

# ---- PyQt6 hidden imports ------------------------------------
# These are not auto-detected because PyQt6 loads them dynamically.
_qt_hidden = [
    "PyQt6.QtCore",
    "PyQt6.QtGui",
    "PyQt6.QtWidgets",
    "PyQt6.QtMultimedia",
    "PyQt6.sip",
]

# Collect PyQt6 platform / style / imageformat plugins as data
_qt_data = []
try:
    import PyQt6
    _qt_root = Path(PyQt6.__file__).parent
    for sub in ("Qt6/plugins/platforms",
                "Qt6/plugins/styles",
                "Qt6/plugins/imageformats",
                "Qt6/plugins/multimedia",
                "Qt6/plugins/tls"):
        src = _qt_root / sub
        if src.is_dir():
            _qt_data.append((str(src), sub.replace("Qt6/plugins/", "qt6_plugins/")))
except Exception:
    pass

# ---- soundfile native library --------------------------------
_sf_hidden = []
_sf_data = []
try:
    import soundfile
    _sf_root = Path(soundfile.__file__).parent
    # libsndfile DLL
    for dll in _sf_root.glob("*.dll"):
        _sf_data.append((str(dll), "."))
    for dll in _sf_root.glob("*.pyd"):
        _sf_data.append((str(dll), "."))
    _sf_hidden.append("_soundfile_data")
except Exception:
    pass

# ---- Assemble ------------------------------------------------
a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=[*_qt_data, *_sf_data],
    hiddenimports=_qt_hidden + _sf_hidden + [
        # numpy
        "numpy._core.multiarray",
        "numpy._core.umath",
        "numpy.linalg",
        # mutagen (MP4, Ogg, etc.)
        "mutagen.mp4",
        "mutagen.oggopus",
        "mutagen.flac",
        "mutagen.id3",
        # openai
        "httpx",
        "httpcore",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "matplotlib",
        "scipy",
        "pandas",
        "PIL",
        "cv2",
        "test",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="lrc-maker",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,                # GUI app -- no console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,                    # Set to "icon.ico" if you add one
)
