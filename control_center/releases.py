"""Shared validation for the existing Control Center release repositories."""
from datetime import datetime, timezone

from control_center.domain import ControlCenterValidationError
from control_center.sanitization import contains_secret_material

METADATA_FIELDS = frozenset({
    "version", "update_channel", "platform", "architecture", "build_commit", "artifact_sha256",
    "artifact_size", "release_notes", "minimum_supported_version", "mandatory", "code_signing", "signer_thumbprint",
})


def validate_release_metadata(metadata: object) -> dict:
    from app.updates.contracts import ReleaseManifest, UpdateValidationError
    if not isinstance(metadata, dict) or set(metadata) != METADATA_FIELDS:
        raise ControlCenterValidationError("Os metadados da versão são inválidos.")
    try:
        ReleaseManifest.parse({**metadata, "schema_version": 1, "release_id": "00000000-0000-4000-8000-000000000000",
                               "sequence": 1, "published_at": datetime.now(timezone.utc).isoformat()})
    except UpdateValidationError:
        raise ControlCenterValidationError("Os metadados da versão são inválidos.") from None
    if contains_secret_material(metadata["release_notes"]):
        raise ControlCenterValidationError("O resumo da versão contém conteúdo protegido.")
    return dict(metadata)


def release_record(row: dict) -> dict:
    metadata = validate_release_metadata({key: row.get(key) for key in METADATA_FIELDS})
    if row.get("status") not in {"draft", "verified", "published", "withdrawn"}:
        raise ControlCenterValidationError("O estado da versão é inválido.")
    return {**metadata, **{key: row.get(key) for key in (
        "id", "sequence", "status", "created_at", "verified_at", "published_at",
    )}}


def installation_release_status(installation, releases: tuple[dict, ...]) -> str:
    from app.updates.contracts import UpdateValidationError, version_parts
    state = installation.metadata.get("update_state")
    if state in {"DOWNLOADING", "READY", "INSTALLING", "RESTARTING", "FAILED", "ROLLED_BACK", "ROLLBACK_REQUIRED"}:
        return {"DOWNLOADING": "Baixando", "READY": "Pronta para instalar", "INSTALLING": "Atualizando",
                "RESTARTING": "Validando atualização", "ROLLED_BACK": "Versão anterior recuperada",
                "FAILED": "Falhou", "ROLLBACK_REQUIRED": "Requer recuperação"}[state]
    channel = installation.metadata.get("update_channel")
    latest = next((r for r in releases if r["status"] == "published" and r["update_channel"] == channel), None)
    if latest is None:
        return "Sem release para comparar"
    try:
        installed = version_parts(installation.version)
        if installed >= version_parts(latest["version"]):
            return "Atualizada"
        if latest["mandatory"] or installed < version_parts(latest["minimum_supported_version"]):
            return "Desatualizada · atualização necessária"
        return "Atualização disponível"
    except UpdateValidationError:
        return "Versão não informada"
