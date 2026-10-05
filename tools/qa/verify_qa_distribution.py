"""Fail closed if a disposable QA bundle points at PROD or carries local data."""
from __future__ import annotations

import json
from pathlib import Path
import sys

from PyInstaller.archive.readers import CArchiveReader


QA_REF = b"nexpointqaisolatelab"
QA_ORIGIN = b"https://qa.nexpoint.test:56430"
PROD_REF = b"scfncgaiovztrbgrcvkt"
FORBIDDEN_SUFFIXES = {".db", ".sqlite", ".sqlite3", ".log", ".dpapi", ".pem", ".key", ".pfx"}


def packed_target(executable: Path) -> bytes:
    archive = CArchiveReader(str(executable))
    targets: list[bytes] = []
    expanded = 0
    for name, member in archive.toc.items():
        if member[2] > 256 * 1024 * 1024:
            raise ValueError("QA packed member too large")
        data = archive.extract(name)
        expanded += len(data)
        if expanded > 2 * 1024 * 1024 * 1024 or PROD_REF in data:
            raise ValueError("PROD reference or excessive packed data")
        if member[4] != "z":
            continue
        pyz = archive.open_embedded_archive(name)
        for module in pyz.toc:
            raw = pyz.extract(module, raw=True)
            if raw is None:
                continue
            expanded += len(raw)
            if expanded > 2 * 1024 * 1024 * 1024 or PROD_REF in raw:
                raise ValueError("PROD reference or excessive packed modules")
            if module == "app.core._build_target":
                targets.append(raw)
    if len(targets) != 1 or targets[0] is None:
        raise ValueError("QA compiled target missing")
    return targets[0]


def verify(bundle: Path) -> None:
    bundle = bundle.resolve(strict=True)
    manifest = json.loads((bundle / "_internal" / "build-manifest.json").read_text(encoding="utf-8-sig"))
    if (manifest.get("schema_version") != 1 or manifest.get("environment") != "qa"
            or manifest.get("channel") != "QA" or manifest.get("supabase_project_ref") != QA_REF.decode()
            or manifest.get("cloud_origin") != QA_ORIGIN.decode()
            or manifest.get("update_channel") != "internal"):
        raise ValueError("QA manifest mismatch")
    entries = list(bundle.rglob("*"))
    if any(item.is_symlink() or (hasattr(item, "is_junction") and item.is_junction()) for item in entries):
        raise ValueError("QA bundle contains links")
    for item in entries:
        if not item.is_file():
            continue
        if item.name.startswith(".env") or item.suffix.lower() in FORBIDDEN_SUFFIXES:
            raise ValueError("QA bundle contains local data")
        if item.stat().st_size > 700 * 1024 * 1024:
            raise ValueError("QA bundle member too large")
        if PROD_REF in item.read_bytes():
            raise ValueError("PROD reference found in QA bundle")
    for name in ("NexPointERP.exe", "NexPointUpdater.exe"):
        target = packed_target(bundle / name)
        if QA_REF not in target or QA_ORIGIN not in target or PROD_REF in target:
            raise ValueError("compiled QA target mismatch")
    profile = bundle / "activation-profile.json"
    if not profile.is_file():
        raise ValueError("public QA activation profile missing")
    value = json.loads(profile.read_text(encoding="utf-8-sig"))
    if value.get("tenant_key") != "qa-nexpoint-lab" or value.get("installation_key") != "qa-win-clean-04":
        raise ValueError("QA activation profile mismatch")


if __name__ == "__main__":
    try:
        verify(Path(sys.argv[1]))
    except (IndexError, OSError, ValueError, KeyError, TypeError) as error:
        print(f"QA distribution rejected: {type(error).__name__}", file=sys.stderr)
        raise SystemExit(1) from None
    print("QA distribution boundary passed")
