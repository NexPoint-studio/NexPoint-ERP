"""Server-only private Storage upload and independent round-trip verification."""
from datetime import datetime, timezone
import http.client
import json
from pathlib import Path
import re
import ssl
import tempfile

from app.updates.contracts import ReleaseManifest, UpdateValidationError
from app.updates.verification import verify_installable_artifact
from control_center.domain import ControlCenterValidationError
from control_center.releases import validate_release_metadata

# Keep the chosen storage compatible with Supabase Free. Increasing the
# download safety ceiling does not increase the hosted project's quota.
RELEASE_STORAGE_MAX_BYTES = 50 * 1024 * 1024


class SupabaseReleaseStorage:
    def __init__(self, project_ref: str, service_role_key: str, *, connection_factory=None):
        if not isinstance(project_ref, str) or not re.fullmatch(r"[a-z0-9]{20}", project_ref):
            raise ControlCenterValidationError("Origem do armazenamento inválida.")
        if not isinstance(service_role_key, str) or len(service_role_key) < 32 or service_role_key.startswith("sb_publishable_"):
            raise ControlCenterValidationError("Credencial server-only do armazenamento inválida.")
        self._host = f"{project_ref}.supabase.co"
        self._key = service_role_key
        self._connection_factory = connection_factory

    def _connection(self):
        if self._connection_factory:
            return self._connection_factory()
        context = ssl.create_default_context()
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        return http.client.HTTPSConnection(self._host, timeout=60, context=context)

    def _headers(self) -> dict[str, str]:
        headers = {"apikey": self._key, "User-Agent": "NexPoint-Release-Publisher/1"}
        if not self._key.startswith("sb_secret_"):
            headers["Authorization"] = f"Bearer {self._key}"
        return headers

    def upload_and_verify(self, artifact: Path, metadata: dict, *, signature_reader=None) -> None:
        metadata = validate_release_metadata(metadata)
        if metadata["artifact_size"] > RELEASE_STORAGE_MAX_BYTES:
            raise ControlCenterValidationError("O instalador excede o limite de 50 MB do armazenamento configurado.")
        manifest = ReleaseManifest.parse({**metadata, "schema_version": 1, "sequence": 1,
            "release_id": "00000000-0000-4000-8000-000000000000", "published_at": datetime.now(timezone.utc).isoformat()})
        artifact = Path(artifact)
        try:
            verify_installable_artifact(artifact, manifest, signature_reader=signature_reader)
            # Content-addressed immutable object. Never use x-upsert:true: an
            # existing official artifact must be byte-identical or fail below.
            path = "/storage/v1/object/nexpoint-releases/" + manifest.object_path
            connection = self._connection()
            try:
                connection.putrequest("POST", path)
                headers = {**self._headers(), "Content-Type": "application/octet-stream",
                           "Content-Length": str(manifest.artifact_size), "x-upsert": "false"}
                for key, value in headers.items():
                    connection.putheader(key, value)
                connection.endheaders()
                with artifact.open("rb") as stream:
                    sent = 0
                    while block := stream.read(min(1024 * 1024, manifest.artifact_size - sent + 1)):
                        sent += len(block)
                        if sent > manifest.artifact_size:
                            raise UpdateValidationError("O arquivo mudou durante o envio.")
                        connection.send(block)
                if sent != manifest.artifact_size:
                    raise UpdateValidationError("O arquivo mudou durante o envio.")
                response = connection.getresponse()
                status = response.status
                raw = response.read(8193)
                # Supabase Storage may express an immutable-object conflict as
                # 400/409. Verify the existing bytes independently in either case.
                duplicate = False
                if status in {400, 409} and len(raw) <= 8192:
                    try:
                        parsed = json.loads(raw)
                        duplicate = isinstance(parsed, dict) and parsed.get("error") in {"Duplicate", "KeyAlreadyExists"}
                    except (ValueError, UnicodeError):
                        pass
                if status not in {200, 201} and not duplicate:
                    raise UpdateValidationError("O armazenamento recusou o envio da versão.")
            finally:
                connection.close()
            connection = self._connection()
            try:
                connection.request("GET", "/storage/v1/object/authenticated/nexpoint-releases/" + manifest.object_path,
                                   headers=self._headers())
                response = connection.getresponse()
                if response.status != 200 or response.getheader("Content-Length") != str(manifest.artifact_size):
                    raise UpdateValidationError("O arquivo armazenado não corresponde à versão.")
                with tempfile.TemporaryDirectory(prefix="nexpoint-release-verification-") as directory:
                    downloaded = Path(directory) / "verify.exe"
                    received = 0
                    with downloaded.open("xb") as stream:
                        while block := response.read(min(1024 * 1024, manifest.artifact_size - received + 1)):
                            received += len(block)
                            if received > manifest.artifact_size:
                                raise UpdateValidationError("O arquivo armazenado excedeu o limite.")
                            stream.write(block)
                    verify_installable_artifact(downloaded, manifest, signature_reader=signature_reader)
            finally:
                connection.close()
        except (UpdateValidationError, ControlCenterValidationError):
            raise
        except Exception:
            raise ControlCenterValidationError("Não foi possível verificar o arquivo no armazenamento oficial.") from None


def prepare_official_release(repository, storage: SupabaseReleaseStorage, *, actor_id: str,
                             artifact: Path, metadata: dict, signature_reader=None) -> dict:
    actor = repository.get_platform_user(actor_id)
    if actor is None or not actor.active or actor.role != "platform_admin":
        raise ControlCenterValidationError("Somente platform_admin ativo pode preparar uma versão.")
    metadata = validate_release_metadata(metadata)
    draft = repository.create_release(actor_id, metadata)
    storage.upload_and_verify(artifact, metadata, signature_reader=signature_reader)
    repository.verify_release(actor_id, draft["release_id"], sha256=metadata["artifact_sha256"], size=metadata["artifact_size"])
    return {"release_id": draft["release_id"], "status": "published" if draft["status"] == "published" else "verified", "version": metadata["version"],
            "update_channel": metadata["update_channel"]}
