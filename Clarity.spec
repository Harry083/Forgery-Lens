# PyInstaller build for the Clarity desktop app:  python -m PyInstaller --clean --noconfirm Clarity.spec
#
# Builds a one-folder app, dist/Clarity/ (Clarity.exe plus its libraries). It runs in place, unlike a one-file
# build that unpacks itself to a temp folder on every launch, which is what antivirus heuristics distrust.
# On Windows the folder is then wrapped in a normal installer by packaging/Clarity.iss (see
# packaging/build-windows.ps1).
# -*- mode: python ; coding: utf-8 -*-
import re
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

VERSION = re.search(r'__version__ = "([^"]+)"', Path("backend/__init__.py").read_text()).group(1)
NUMBERS = tuple(int(n) for n in (re.findall(r"\d+", VERSION) + ["0"] * 4)[:4])

version_info = None
try:  # file properties shown in Explorer (Details tab); Windows-only resource, harmless elsewhere
    from PyInstaller.utils.win32.versioninfo import (FixedFileInfo, StringFileInfo, StringStruct, StringTable,
                                                     VarFileInfo, VarStruct, VSVersionInfo)

    version_info = VSVersionInfo(
        ffi=FixedFileInfo(filevers=NUMBERS, prodvers=NUMBERS),
        kids=[
            StringFileInfo([StringTable("080904B0", [
                StringStruct("CompanyName", "Harry Smallwood"),
                StringStruct("FileDescription", "Clarity: forensic image and video enhancement and authentication"),
                StringStruct("FileVersion", VERSION),
                StringStruct("InternalName", "Clarity"),
                StringStruct("LegalCopyright", "Harry Smallwood"),
                StringStruct("OriginalFilename", "Clarity.exe"),
                StringStruct("ProductName", "Clarity"),
                StringStruct("ProductVersion", VERSION),
            ])]),
            VarFileInfo([VarStruct("Translation", [0x0809, 1200])]),
        ],
    )
except ImportError:
    pass

a = Analysis(
    ["app.py"],
    pathex=[],
    datas=[("frontend", "frontend"), ("clarity.ico", "."), ("clarity.png", ".")],
    # backend/techniques/__init__.py imports each technique by name, which PyInstaller can't see
    hiddenimports=collect_submodules("backend"),
    excludes=["tkinter", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,  # one-folder build: libraries sit next to the exe instead of inside it
    name="Clarity",
    console=False,  # windowed app, no console
    icon="clarity.ico",  # .exe, taskbar and title-bar icon
    version=version_info,
    upx=False,  # UPX-packed executables are flagged by antivirus far more often
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    upx=False,
    name="Clarity",
)
