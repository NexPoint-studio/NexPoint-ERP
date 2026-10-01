"""Owner-side authorization; never distribute this script or its protected vault.

Reads an explicitly selected operational user's existing scrypt verifier without
changing that database. The new local technical owner has an independent random
password stored only in the responsible operator's DPAPI vault. Cloud secrets
remain in the pre-existing Control Center vault and are never written or printed.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import secrets
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.config import PRODUCTION_SUPABASE_PROJECT_REF
from app.core.installation_identity import WindowsDpapiProtector
from app.core.security import hash_password, verify_password
from app.services.installation_activation import ActivationProfile, _HASH, _regular
from scripts.provision_prod import SupabaseProvisioningClient


def prepare(args):
    profile = ActivationProfile(args.tenant, args.installation, args.company, args.username)
    database = Path(args.operator_database).resolve()
    _regular(database)
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("pragma query_only=on")
        rows = db.execute("select u.password_hash, r.code from users u join user_roles ur on ur.user_id=u.id join roles r on r.id=ur.role_id where u.email=? and u.active=1", (profile.username,)).fetchall()
    if len(rows) != 1 or rows[0][1] != "user" or not _HASH.fullmatch(rows[0][0]):
        raise ValueError("approved operational verifier unavailable")
    operator_hash = rows[0][0]
    protector = WindowsDpapiProtector()
    local_root = Path(os.environ["LOCALAPPDATA"]) / "NexPoint/ERP/credentials"
    cloud = json.loads(protector.unprotect((local_root / "control-center.dpapi").read_bytes()))
    ref = PRODUCTION_SUPABASE_PROJECT_REF
    client = SupabaseProvisioningClient("https://" + ref + ".supabase.co", cloud["CONTROL_CENTER_SUPABASE_SERVICE_ROLE_KEY"], ref)
    tenant = client.get_tenant(profile.tenant_key)
    if not tenant or tenant["status"] != "active" or tenant["kind"] not in {"internal", "customer"}:
        raise ValueError("tenant unavailable")
    if client.get_installation(profile.installation_key):
        raise ValueError("installation already exists; requires review")
    records = client._request("GET", "/rest/v1/np_installation_activation_grants", query=(
        ("select", "*"), ("username", "eq." + profile.username), ("limit", 2)))
    if not isinstance(records, list) or len(records) > 1:
        raise ValueError("ambiguous authorization")
    owner_path = local_root / ("activation-owner-" + profile.installation_key + ".dpapi")
    _regular(owner_path)
    if owner_path.exists():
        owner = json.loads(protector.unprotect(owner_path.read_bytes()))
        if (owner["installation_key"] != profile.installation_key or owner["tenant_key"] != profile.tenant_key
                or not verify_password(owner["password"], owner["password_hash"])):
            raise ValueError("owner vault mismatch")
    else:
        if records:
            raise ValueError("grant exists without responsible owner's vault; no rotation")
        password = secrets.token_urlsafe(24)
        owner = {"schema_version": 1, "installation_key": profile.installation_key,
                 "tenant_key": profile.tenant_key, "username": "technical." + profile.installation_key,
                 "password": password, "password_hash": hash_password(password)}
        with owner_path.open("xb") as stream:
            stream.write(protector.protect(json.dumps(owner).encode()))
            stream.flush()
            os.fsync(stream.fileno())
        if json.loads(protector.unprotect(owner_path.read_bytes())) != owner:
            raise ValueError("owner vault roundtrip failed")
    expected = {"username": profile.username, "display_name": profile.company_name,
        "password_hash": operator_hash, "owner_username": owner["username"],
        "owner_display_name": "Responsável técnico NexPoint", "owner_password_hash": owner["password_hash"],
        "tenant_id": tenant["id"], "installation_key": profile.installation_key,
        "installation_label": args.label, "company_name": profile.company_name,
        "environment": "prod", "local_role": "user", "active": True, "can_activate": True}
    if records:
        row = records[0]
        if (any(row.get(k) != v for k, v in expected.items()) or row.get("revoked_at") or row.get("consumed_at")
                or datetime.fromisoformat(row["expires_at"]) <= datetime.now(timezone.utc)):
            raise ValueError("existing authorization differs; no change performed")
    else:
        expected["expires_at"] = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
        created = client._request("POST", "/rest/v1/np_installation_activation_grants", payload=expected,
            prefer="return=representation", expected=(201,))
        if not isinstance(created, list) or len(created) != 1:
            raise ValueError("authorization creation not confirmed")
        row = created[0]
        if any(row.get(k) != v for k, v in expected.items() if k != "expires_at"):
            raise ValueError("authorization readback mismatch")
    return {"authorization_prepared": True, "installation_created": False,
        "tenant": profile.tenant_key, "installation": profile.installation_key,
        "label": args.label, "username": profile.username, "expires_at": row["expires_at"],
        "operator_password_unchanged": True, "owner_credential_protected_locally": True,
        "owner_vault": str(owner_path), "source_database_unchanged": True}


def main():
    parser = argparse.ArgumentParser(description="Prepare scoped first-device authorization on the responsible owner's PC.")
    for name in ("tenant", "installation", "company", "username", "label", "operator-database"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--execute", action="store_true", required=True)
    args = parser.parse_args()
    try:
        report = prepare(args)
    except Exception:
        print("ERRO: a autorização não foi preparada ou reconciliada; nenhum secret foi exibido.", file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
