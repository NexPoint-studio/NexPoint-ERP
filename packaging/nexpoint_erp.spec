# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller recipe for the private NexPoint ERP Windows distribution."""

from pathlib import Path
import os

from PyInstaller.utils.hooks import collect_submodules


# PyInstaller exposes SPECPATH as the directory that contains this recipe.
project_root = Path(SPECPATH).resolve().parent
manifest = Path(
    os.environ.get(
        "NEXPOINT_BUILD_MANIFEST",
        project_root / "artifacts" / "windows-build" / "build-manifest.json",
    )
).resolve()
if not manifest.is_file():
    raise SystemExit("NEXPOINT_BUILD_MANIFEST must identify the generated build manifest")

datas = [
    (str(project_root / "templates"), "templates"),
    (str(project_root / "static"), "static"),
    (str(project_root / "control_center" / "templates"), "control_center/templates"),
    (str(project_root / "control_center" / "static"), "control_center/static"),
    (str(manifest), "."),
]

hidden_imports = sorted(
    set(
        collect_submodules("webview")
        + collect_submodules("uvicorn")
        + [
            "sqlalchemy.dialects.sqlite.pysqlite",
            "tzdata",
        ]
    )
)

analysis = Analysis(
    [str(project_root / "run_desktop.py")],
    pathex=[str(project_root)],
    binaries=[],
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "pytest",
        "httpx",
        "tkinter",
    ],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="NexPointERP",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version=str(project_root / "packaging" / "windows_version_info.txt"),
)

collection = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="NexPointERP",
)
