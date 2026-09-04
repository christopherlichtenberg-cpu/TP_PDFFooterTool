# -*- mode: python ; coding: utf-8 -*-
"""
Builds dist/AffStamp/ containing BOTH executables from one shared runtime
folder:

    AffStamp.exe       the window   (console=False)
    AffStamp-cli.exe   the console  (console=True)

Two Analysis passes feed a single COLLECT, so pymupdf, Pillow and Tcl/Tk are
shipped once rather than twice - about 88 MB instead of ~180 MB.

    py -m PyInstaller --noconfirm --clean AffStamp.spec
"""

EXCLUDES = [
    "numpy", "scipy", "matplotlib", "pandas",       # not used; huge
    "pytest", "setuptools", "pip", "wheel",
    "PIL.ImageQt", "PyQt5", "PyQt6", "PySide2", "PySide6",
    "IPython", "notebook", "sqlite3", "pydoc_data",
]

gui = Analysis(
    ["affstamp_gui.py"],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=["affstamp"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
)

cli = Analysis(
    ["affstamp.py"],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # The console build never opens a window, so Tk can go.
    excludes=EXCLUDES + ["tkinter", "affstamp_gui"],
    noarchive=False,
)

gui_pyz = PYZ(gui.pure)
cli_pyz = PYZ(cli.pure)

gui_exe = EXE(
    gui_pyz,
    gui.scripts,
    [],
    exclude_binaries=True,
    name="AffStamp",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                  # UPX-packed exes trip antivirus even harder
    console=False,              # no console window behind the GUI
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version="version_info.txt",
)

cli_exe = EXE(
    cli_pyz,
    cli.scripts,
    [],
    exclude_binaries=True,
    name="AffStamp-cli",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version="version_info.txt",
)

coll = COLLECT(
    gui_exe,
    gui.binaries,
    gui.datas,
    cli_exe,
    cli.binaries,
    cli.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="AffStamp",
)
