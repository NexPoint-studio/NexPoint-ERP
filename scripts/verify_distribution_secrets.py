"""Reject a Windows distribution containing local configuration or secret values."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys
import zipfile

MAX_MEMBER_BYTES = 256 * 1024 * 1024
MAX_EXPANDED_BYTES = 2 * 1024 * 1024 * 1024


SENSITIVE_MARKERS = (
    b"SUPABASE_SERVICE_ROLE_KEY=",
    b"NEXA_ERP_BRIDGE_SECRET=",
    b"CONTROL_CENTER_SESSION_SECRET=",
    b"ERP_SESSION_SECRET=",
    b"ERP_ADMIN_PASSWORD=",
)
SENSITIVE_ENV_KEY = re.compile(
    r"(?:PASSWORD|PASSWD|PASSPHRASE|SECRET|TOKEN|API_?KEY|SERVICE_ROLE_?KEY|PRIVATE_?KEY)",
    re.IGNORECASE,
)


def local_secret_values(root: Path) -> tuple[bytes, ...]:
    values: list[bytes] = []
    for name in (".env", ".env.local"):
        path = root / name
        if not path.is_file():
            continue
        for raw in path.read_text(encoding="utf-8-sig").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if not SENSITIVE_ENV_KEY.search(key.strip()):
                continue
            encoded = value.strip().encode("utf-8")
            if len(encoded) >= 12 and not encoded.startswith(b"SUBSTITUA_"):
                values.append(encoded)
    return tuple(values)


def distribution_chunks(path: Path):
    """Inspect compressed official executables without executing their code."""
    yield path.read_bytes()
    expanded = 0
    if path.name in {"NexPointERP.exe", "NexPointUpdater.exe"}:
        # Build dependency only. raw=True returns marshalled bytes, never code
        # objects for execution. Fail closed if an official executable cannot
        # be opened, including when the inspector is unavailable.
        from PyInstaller.archive.readers import CArchiveReader
        archive = CArchiveReader(str(path))
        for name, entry in archive.toc.items():
            if entry[2] > MAX_MEMBER_BYTES:
                raise ValueError("packed member too large")
            data = archive.extract(name)
            expanded += len(data)
            if expanded > MAX_EXPANDED_BYTES:
                raise ValueError("packed distribution too large")
            yield data
            if entry[4] == "z":
                modules = archive.open_embedded_archive(name)
                for module in modules.toc:
                    raw = modules.extract(module, raw=True)
                    if raw is None:
                        continue
                    expanded += len(raw)
                    if len(raw) > MAX_MEMBER_BYTES or expanded > MAX_EXPANDED_BYTES:
                        raise ValueError("packed modules too large")
                    yield raw
    elif path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as archive:
            for member in archive.infolist():
                expanded += member.file_size
                if member.file_size > MAX_MEMBER_BYTES or expanded > MAX_EXPANDED_BYTES:
                    raise ValueError("compressed distribution too large")
                yield archive.read(member)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--distribution", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    distribution = args.distribution.resolve()
    if not distribution.is_dir():
        raise SystemExit("distribution directory does not exist")
    secrets = local_secret_values(root)
    checked = 0
    for path in distribution.rglob("*"):
        if not path.is_file():
            continue
        checked += 1
        try:
            for data in distribution_chunks(path):
                if any(marker in data for marker in SENSITIVE_MARKERS):
                    print(f"sensitive configuration marker found in {path.name}", file=sys.stderr)
                    return 2
                if any(value in data for value in secrets):
                    print(f"local secret value found in {path.name}", file=sys.stderr)
                    return 3
        except Exception:
            print(f"could not inspect distribution content in {path.name}", file=sys.stderr)
            return 4
    print(f"distribution secret scan passed ({checked} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
