# PyInstaller build for the Forgery Lens desktop app:  python -m PyInstaller --clean ForgeryLens.spec
# Produces a single file, dist/ForgeryLens.exe (dist/ForgeryLens on Linux/macOS), with the app icon.
# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules

a = Analysis(
    ["app.py"],
    pathex=[],
    datas=[("frontend", "frontend"), ("forgerylens.ico", "."), ("forgerylens.png", ".")],
    # backend/techniques/__init__.py imports each technique by name, which PyInstaller can't see
    hiddenimports=collect_submodules("backend"),
    excludes=["tkinter", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="ForgeryLens",
    console=False,  # windowed app, no console
    icon="forgerylens.ico",  # .exe, taskbar and title-bar icon
    runtime_tmpdir=None,
)
