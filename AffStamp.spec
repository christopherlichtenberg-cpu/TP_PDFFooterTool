# PyInstaller spec - builds TWO executables that share one _internal folder:
#
#   AffStamp.exe       windowed, no console. Double-click this.
#   AffStamp-cli.exe   console. The menu and every command-line option.
#
# Build with:  py -m PyInstaller --noconfirm --clean AffStamp.spec
#
# Sharing one COLLECT keeps the folder at ~90 MB instead of ~180 MB.

from PyInstaller.utils.hooks import collect_all

pymu_datas, pymu_binaries, pymu_hidden = collect_all("pymupdf")

# Excluding these keeps the bundle lean. --collect-all pymupdf otherwise
# drags in pandas (13 MB) and lxml (7 MB), which this tool never uses.
EXCLUDES = [
    "numpy", "scipy", "matplotlib", "pandas", "lxml",
    "PySide6", "PyQt5", "PyQt6", "IPython", "pytest", "setuptools",
    "test", "unittest", "pydoc_data",
]

COMMON = dict(
    pathex=["."],
    binaries=pymu_binaries,
    datas=pymu_datas,
    # affstamp imports pdflinkcheck for link analysis; it sits beside
    # these files rather than on sys.path, so name it explicitly.
    hiddenimports=pymu_hidden + ["pdflinkcheck"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
)

# The GUI entry point imports affstamp, so this analysis is a superset of
# the CLI one - its binaries are what both executables load at runtime.
gui_a = Analysis(["affstamp_gui.py"], **COMMON)
cli_a = Analysis(["affstamp.py"], **COMMON)

gui_pyz = PYZ(gui_a.pure)
cli_pyz = PYZ(cli_a.pure)

gui_exe = EXE(
    gui_pyz, gui_a.scripts, [],
    exclude_binaries=True,
    name="AffStamp",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                 # UPX-packed binaries trip antivirus far more
    console=False,             # windowed
    disable_windowed_traceback=False,
    version="version_info.txt",
)

cli_exe = EXE(
    cli_pyz, cli_a.scripts, [],
    exclude_binaries=True,
    name="AffStamp-cli",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    version="version_info.txt",
)

coll = COLLECT(
    gui_exe,
    cli_exe,
    gui_a.binaries,
    gui_a.datas,
    strip=False,
    upx=False,
    name="AffStamp",
)
