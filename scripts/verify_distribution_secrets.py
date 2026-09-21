"""Reject a Windows distribution containing local configuration or secret values."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


SENSITIVE_MARKERS = (
    b"SUPABASE_SERVICE_ROLE_KEY=",
    b"NEXA_ERP_BRIDGE_SECRET=",
    b"CONTROL_CENTER_SESSION_SECRET=",
    b"ERP_SESSION_SECRET=",
    b"ERP_ADMIN_PASSWORD=",
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
            _key, value = line.split("=", 1)
            encoded = value.strip().encode("utf-8")
            if len(encoded) >= 12 and not encoded.startswith(b"SUBSTITUA_"):
                values.append(encoded)
    return tuple(values)


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
        data = path.read_bytes()
        if any(marker in data for marker in SENSITIVE_MARKERS):
            print(f"sensitive configuration marker found in {path.name}", file=sys.stderr)
            return 2
        if any(value in data for value in secrets):
            print(f"local secret value found in {path.name}", file=sys.stderr)
            return 3
    print(f"distribution secret scan passed ({checked} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
