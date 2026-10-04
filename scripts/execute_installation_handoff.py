"""Owner-side cloud half of an already prepared, backed-up handoff.

Preview is offline. --execute requires the existing DPAPI Control Center vault
and an independently verified backend backup attestation. Never run from the
customer computer. No privileged material is printed or packaged.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sys
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.core.config import PRODUCTION_SUPABASE_PROJECT_REF
from app.core.installation_identity import WindowsDpapiProtector
from control_center.supabase_repository import SupabaseControlCenterRepository

IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{7,119}$")
HASH = re.compile(r"^[0-9a-f]{64}$")


def _read_json(path: Path, *, maximum=8192):
    path = Path(path).expanduser().absolute()
    if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= maximum:
        raise ValueError("arquivo ausente ou inválido")
    return json.loads(path.read_text(encoding="utf-8-sig"))


def validate_prepared_report(value: object) -> dict:
    fields = {"operation_id", "source_tenant_id", "target_tenant_id", "installation_id",
              "backup_sha256", "local_preflight_sha256", "phase"}
    if not isinstance(value, dict) or set(value) != fields or value["phase"] != "prepared":
        raise ValueError("comprovante local inválido")
    if str(UUID(value["operation_id"])) != value["operation_id"]:
        raise ValueError("operação inválida")
    for key in ("source_tenant_id", "target_tenant_id", "installation_id"):
        if not isinstance(value[key], str) or not IDENTIFIER.fullmatch(value[key]):
            raise ValueError("escopo inválido")
    if value["source_tenant_id"] == value["target_tenant_id"]:
        raise ValueError("origem igual ao destino")
    for key in ("backup_sha256", "local_preflight_sha256"):
        if not isinstance(value[key], str) or not HASH.fullmatch(value[key]):
            raise ValueError("hash inválido")
    return value


def verify_backend_backup(attestation_path: Path, report: dict) -> None:
    attestation = _read_json(attestation_path)
    if (not isinstance(attestation, dict) or set(attestation) !=
        {"schema_version", "operation_id", "source_tenant_id", "installation_id",
         "backup_file", "sha256"} or attestation["schema_version"] != 1 or
        attestation["operation_id"] != report["operation_id"] or
        attestation["source_tenant_id"] != report["source_tenant_id"] or
        attestation["installation_id"] != report["installation_id"] or
        not isinstance(attestation["sha256"], str) or not HASH.fullmatch(attestation["sha256"])):
        raise ValueError("atestado do backup backend inválido")
    backup = Path(attestation["backup_file"])
    if not backup.is_absolute() or backup.is_symlink() or not backup.is_file() or backup.stat().st_size < 128:
        raise ValueError("backup backend ausente")
    digest = sha256()
    with backup.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != attestation["sha256"]:
        raise ValueError("backup backend não corresponde ao atestado")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Reassociar instalação após backup e QA")
    parser.add_argument("--prepared-report", type=Path, required=True)
    parser.add_argument("--backend-backup-attestation", type=Path)
    parser.add_argument("--company-name", required=True)
    parser.add_argument("--installation-label", required=True)
    parser.add_argument("--actor", default="nexpoint-admin")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = validate_prepared_report(_read_json(args.prepared_report))
        if (not 1 <= len(args.company_name.strip()) <= 180 or args.company_name != args.company_name.strip()
            or not 1 <= len(args.installation_label.strip()) <= 160
            or args.installation_label != args.installation_label.strip()
            or any(ord(c) < 32 or ord(c) == 127 for c in args.company_name + args.installation_label)):
            raise ValueError("nome ou rótulo inválido")
        if not args.execute:
            print(json.dumps({"validated_local_report": True, "cloud_mutation": False,
                "operation_id": report["operation_id"], "source": report["source_tenant_id"],
                "target": report["target_tenant_id"], "installation": report["installation_id"]}))
            return 0
        if not args.backend_backup_attestation:
            raise ValueError("backup backend obrigatório")
        verify_backend_backup(args.backend_backup_attestation, report)
        local_app_data = os.environ.get("LOCALAPPDATA", "")
        if not local_app_data:
            raise ValueError("cofre local indisponível")
        vault = Path(local_app_data) / "NexPoint" / "ERP" / "credentials" / "control-center.dpapi"
        if vault.is_symlink() or not vault.is_file() or vault.stat().st_size > 65536:
            raise ValueError("cofre local indisponível")
        material = json.loads(WindowsDpapiProtector().unprotect(vault.read_bytes()))
        key = material["CONTROL_CENTER_SUPABASE_SERVICE_ROLE_KEY"]
        repository = SupabaseControlCenterRepository(
            f"https://{PRODUCTION_SUPABASE_PROJECT_REF}.supabase.co", key, initialize=False)
        actor = repository.get_platform_user_by_username(args.actor)
        if actor is None or not actor.active or actor.role != "platform_admin":
            raise ValueError("operador não autorizado")
        result = repository._rpc("np_admin_reassociate_installation", {
            "p_actor_id": actor.id, "p_operation_id": report["operation_id"],
            "p_source_tenant_key": report["source_tenant_id"],
            "p_target_tenant_key": report["target_tenant_id"],
            "p_target_display_name": args.company_name,
            "p_installation_key": report["installation_id"],
            "p_installation_label": args.installation_label,
            "p_backup_sha256": report["backup_sha256"],
            "p_local_preflight_sha256": report["local_preflight_sha256"],
        })
        if (not isinstance(result, dict) or result.get("ok") is not True or
            result.get("state") not in {"cloud_committed", "confirmed"} or
            result.get("operation_id") != report["operation_id"] or
            result.get("tenant_id") != report["target_tenant_id"] or
            result.get("installation_id") != report["installation_id"]):
            raise ValueError("backend recusou a transferência")
        print(json.dumps({"cloud_committed": True, "operation_id": report["operation_id"],
                          "installation": report["installation_id"],
                          "next_step": "retomar no mesmo computador e validar confirmacao"}))
        return 0
    except Exception:
        print("Transferência bloqueada. Preserve os backups e confira os gates, o operador e o escopo. Não repita com outra operação.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
