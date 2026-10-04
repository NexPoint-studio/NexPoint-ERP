"""Owner-side release preparation. Preview is offline; --upload creates a draft.

The existing Windows DPAPI Control Center vault is read only for an upload.
No privileged material is accepted on the command line or sent to the desktop.
Publication remains an explicit action in Control Center / Versões.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.config import PRODUCTION_SUPABASE_PROJECT_REF
from app.core.installation_identity import WindowsDpapiProtector
from app.updates.contracts import ReleaseManifest, UpdateValidationError
from app.updates.installer import file_digest, regular
from app.updates.verification import verify_installable_artifact
from control_center.release_storage import RELEASE_STORAGE_MAX_BYTES, SupabaseReleaseStorage, prepare_official_release
from control_center.releases import validate_release_metadata
from control_center.supabase_repository import SupabaseControlCenterRepository


def release_metadata(artifact: Path, *, notes: str, channel: str, minimum_version: str,
                     mandatory=False, signer=None, signature_reader=None) -> dict:
    artifact = regular(artifact)
    report_path = regular(artifact.parent / "installer-build.json")
    if not artifact.is_file() or not report_path.is_file() or report_path.stat().st_size > 32768:
        raise UpdateValidationError("Selecione o instalador e seu relatório oficial de build.")
    if artifact.stat().st_size > RELEASE_STORAGE_MAX_BYTES:
        raise UpdateValidationError("O instalador excede o limite de 50 MB do armazenamento configurado.")
    report = json.loads(report_path.read_text(encoding="utf-8-sig"))
    if (report.get("environment") != "production" or report.get("channel") != "PROD"
        or report.get("scope") != "current-user" or report.get("updater_included") is not True
        or regular(Path(report.get("installer", ""))) != artifact
        or report.get("installer_sha256") != file_digest(artifact)):
        raise UpdateValidationError("O instalador diverge da build aprovada ou não inclui o atualizador.")
    metadata = validate_release_metadata({
        "version": report.get("version"), "build_commit": report.get("commit"),
        "update_channel": channel, "platform": "windows", "architecture": "x64",
        "artifact_sha256": report["installer_sha256"], "artifact_size": artifact.stat().st_size,
        "release_notes": notes, "minimum_supported_version": minimum_version, "mandatory": mandatory,
        "code_signing": "AUTHENTICODE" if signer else "CODE_SIGNING_PENDING", "signer_thumbprint": signer,
    })
    manifest = ReleaseManifest.parse({**metadata, "schema_version": 1, "sequence": 1,
        "release_id": "00000000-0000-4000-8000-000000000000", "published_at": datetime.now(timezone.utc).isoformat()})
    verify_installable_artifact(artifact, manifest, signature_reader=signature_reader)
    return metadata


def main(argv=None):
    parser = argparse.ArgumentParser(description="Preparar versão oficial do NexPoint ERP")
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--notes", type=Path, required=True)
    parser.add_argument("--channel", choices=("stable", "beta", "internal"), required=True)
    parser.add_argument("--minimum-version", required=True)
    parser.add_argument("--mandatory", action="store_true")
    parser.add_argument("--signer-thumbprint")
    parser.add_argument("--actor", default="nexpoint-admin")
    parser.add_argument("--upload", action="store_true")
    args = parser.parse_args(argv)
    try:
        notes_path = regular(args.notes)
        if not notes_path.is_file() or notes_path.stat().st_size > 16000:
            raise ValueError
        metadata = release_metadata(args.artifact, notes=notes_path.read_text(encoding="utf-8-sig"),
            channel=args.channel, minimum_version=args.minimum_version, mandatory=args.mandatory,
            signer=args.signer_thumbprint)
        if not args.upload:
            print(json.dumps({"validated_locally": True, "uploaded": False, "metadata": metadata}, ensure_ascii=False))
            return 0
        vault = regular(Path(os.environ["LOCALAPPDATA"]) / "NexPoint/ERP/credentials/control-center.dpapi")
        if not vault.is_file() or vault.stat().st_size > 65536:
            raise ValueError
        material = json.loads(WindowsDpapiProtector().unprotect(vault.read_bytes()))
        key = material["CONTROL_CENTER_SUPABASE_SERVICE_ROLE_KEY"]
        reference = PRODUCTION_SUPABASE_PROJECT_REF
        repository = SupabaseControlCenterRepository(f"https://{reference}.supabase.co", key, initialize=False)
        actor = repository.get_platform_user_by_username(args.actor)
        if actor is None or not actor.active or actor.role != "platform_admin":
            raise ValueError
        result = prepare_official_release(repository, SupabaseReleaseStorage(reference, key),
            actor_id=actor.id, artifact=args.artifact, metadata=metadata)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception:
        print("A preparação foi bloqueada. Verifique build, assinatura, metadados e acesso do operador. Nenhuma versão foi publicada.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
