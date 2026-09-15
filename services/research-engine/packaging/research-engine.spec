# PyInstaller spec of the research worker sidecar (phase 14, D-088).
#
# A one-folder build: `dist/research-engine/research-engine.exe` plus its libraries. The Tauri bundle
# copies the folder next to the app (`<install dir>/research-engine/`), where the desktop core looks
# for it. A one-file build would unpack ~150 MB to %TEMP% on every start (slower, and scanned again
# by antivirus each time).
#
# Build: `pnpm build:sidecar` (runs PyInstaller from the locked worker environment).

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

datas = collect_data_files("research_engine", includes=["**/*.sql", "**/*.txt"])
datas += collect_data_files("phonenumbers")
hiddenimports = collect_submodules("research_engine") + collect_submodules("phonenumbers")

a = Analysis(
    ["../src/research_engine/__main__.py"],
    pathex=["../src"],
    datas=datas,
    hiddenimports=hiddenimports,
    # development tools that must never ship
    excludes=["tkinter", "mypy", "mypyc", "pytest", "_pytest", "ruff", "pygments", "IPython"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="research-engine",
    # a console program: the core talks to it over stdin/stdout and starts it without a window
    console=True,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="research-engine", upx=False)
