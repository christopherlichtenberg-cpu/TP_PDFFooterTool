# -*- mode: python ; coding: utf-8 -*-
"""
Builds dist/PDFLinkCheck/ - the standalone link auditor, console only.

Much smaller than the AffStamp build: no Pillow, no Tcl/Tk, PyMuPDF only.

    py -m PyInstaller --noconfirm --clean PDFLinkCheck.spec
"""

a = Analysis(
    ["pdflinkcheck.py"],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "PIL", "pillow",                  # pdflinkcheck never renders a page
        "tkinter", "affstamp", "affstamp_gui",
        "numpy", "scipy", "matplotlib", "pandas",
        "pytest", "setuptools", "pip", "wheel",
        "PyQt5", "PyQt6", "PySide2", "PySide6",
        "IPython", "notebook", "pydoc_data",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PDFLinkCheck",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                            # UPX makes antivirus worse
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version="pdflinkcheck_version.txt",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="PDFLinkCheck",
)
