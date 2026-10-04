# -*- mode: python ; coding: utf-8 -*-
"""Standalone helper copied outside the program tree before installation."""
from pathlib import Path
import os

project_root = Path(SPECPATH).resolve().parent
manifest = Path(os.environ["NEXPOINT_BUILD_MANIFEST"]).resolve()
if not manifest.is_file():
    raise SystemExit("The updater requires the same approved build manifest as ERP")
analysis = Analysis(
    [str(project_root / "run_updater.py")], pathex=[str(project_root)],
    binaries=[], datas=[(str(manifest), ".")],
    hiddenimports=["sqlalchemy.dialects.sqlite.pysqlite", "tzdata"],
    hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=["pytest", "httpx", "tkinter", "webview"], noarchive=False, optimize=1,
)
pyz = PYZ(analysis.pure)
exe = EXE(
    pyz, analysis.scripts, analysis.binaries, analysis.datas, [],
    name="NexPointUpdater", debug=False, bootloader_ignore_signals=False,
    strip=False, upx=False, console=False, disable_windowed_traceback=True,
    argv_emulation=False, target_arch=None, codesign_identity=None, entitlements_file=None,
    version=str(project_root / "packaging" / "windows_version_info.txt"),
)
