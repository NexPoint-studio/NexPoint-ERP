"""Publish the Windows QA Build B only to the isolated local Supabase QA stack.

Default invocation validates the artifact without writing to QA. Publication
requires both --publish and recorded Build A guest acceptance. This helper has
no production credential path, hostname, or configurable network destination.

The ignored --gate-report JSON records schema_version=1, vm="NexPoint-Windows-QA",
origin="https://qa.nexpoint.test:56430", build_a_version="1.1.0",
build_a_commit=<40 lowercase hex>, and a gates object with PASS for
clean_install, activation, empresa, admin_lock, recovery, heartbeat and sync.
The report must live below artifacts/windows-qa-vm.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import http.client
import json
from pathlib import Path
import re
import subprocess
import sys
from uuid import UUID

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.updates.contracts import ReleaseManifest
from app.updates.installer import file_digest, regular
from app.updates.verification import verify_installable_artifact
from control_center.release_storage import RELEASE_STORAGE_MAX_BYTES, SupabaseReleaseStorage
from control_center.releases import validate_release_metadata
from tools.qa.verify_qa_origin import run as verify_qa_origin


QA_ROOT = ROOT / "artifacts" / "qa-handoff-backend-20261004"
QA_INSTALLERS = ROOT / "artifacts" / "windows-qa-installer"
QA_GATES = ROOT / "artifacts" / "windows-qa-vm"
QA_DB_CONTAINER = "supabase_db_nexpoint-erp-handoff-qa"
QA_PROJECT_REF = "nexpointqaisolatelab"
QA_ORIGIN = "https://qa.nexpoint.test:56430"
QA_LOOPBACK = "127.0.0.1"
QA_API_PORT = 56421
QA_VERSION = "1.1.2"
BUILD_A_VERSION = "1.1.0"
GUEST_GATES = frozenset({"clean_install", "activation", "empresa", "admin_lock", "recovery", "heartbeat", "sync"})


class QAPublicationError(RuntimeError):
    """Failure messages never contain credentials or remote response bodies."""


def _json_file(path: Path, *, limit: int) -> dict:
    if not path.is_file() or path.stat().st_size > limit:
        raise QAPublicationError("Arquivo de evidencia QA ausente ou grande demais.")
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, ValueError):
        raise QAPublicationError("Arquivo de evidencia QA invalido.") from None
    if not isinstance(value, dict):
        raise QAPublicationError("Arquivo de evidencia QA invalido.")
    return value


def _inside(path: Path, directory: Path) -> Path:
    path = regular(Path(path))
    if not path.is_relative_to(directory):
        raise QAPublicationError("O arquivo nao pertence aos artefatos QA esperados.")
    return path


def build_b_metadata(artifact: Path) -> dict:
    artifact = _inside(artifact, QA_INSTALLERS)
    if artifact.name != "NexPointERP-QA-Setup.exe" or not artifact.is_file():
        raise QAPublicationError("Selecione o instalador oficial da Build B QA.")
    report = _json_file(_inside(artifact.parent / "installer-build.json", QA_INSTALLERS), limit=32768)
    commit = report.get("commit")
    digest = file_digest(artifact)
    if (
        report.get("installer") != str(artifact)
        or report.get("installer_sha256") != digest
        or report.get("environment") != "qa"
        or report.get("channel") != "QA"
        or report.get("version") != QA_VERSION
        or report.get("scope") != "current-user"
        or report.get("updater_included") is not True
        or report.get("signing_status") != "NotSigned"
        or not isinstance(commit, str)
        or re.fullmatch(r"[a-f0-9]{40}", commit) is None
        or report.get("build") != "QA-B-" + commit[:12]
        or not 1 <= artifact.stat().st_size <= RELEASE_STORAGE_MAX_BYTES
    ):
        raise QAPublicationError("O instalador B diverge da build QA aprovada ou do limite de 50 MiB.")
    metadata = validate_release_metadata({
        "version": QA_VERSION,
        "update_channel": "internal",
        "platform": "windows",
        "architecture": "x64",
        "build_commit": commit,
        "artifact_sha256": digest,
        "artifact_size": artifact.stat().st_size,
        "release_notes": "Atualizacao interna QA da Build A para a Build B.",
        "minimum_supported_version": BUILD_A_VERSION,
        "mandatory": False,
        "code_signing": "CODE_SIGNING_PENDING",
        "signer_thumbprint": None,
    })
    manifest = ReleaseManifest.parse({**metadata, "schema_version": 1, "sequence": 1,
        "release_id": "00000000-0000-4000-8000-000000000000",
        "published_at": datetime.now(timezone.utc).isoformat()})
    verify_installable_artifact(artifact, manifest)
    return metadata


def require_build_a_gates(report_path: Path) -> None:
    report = _json_file(_inside(report_path, QA_GATES), limit=32768)
    gates = report.get("gates")
    if (
        report.get("schema_version") != 1
        or report.get("vm") != "NexPoint-Windows-QA"
        or report.get("origin") != QA_ORIGIN
        or report.get("build_a_version") != BUILD_A_VERSION
        or not isinstance(report.get("build_a_commit"), str)
        or re.fullmatch(r"[a-f0-9]{40}", report["build_a_commit"]) is None
        or not isinstance(gates, dict)
        or any(gates.get(name) != "PASS" for name in GUEST_GATES)
    ):
        raise QAPublicationError("Os gates da Build A na VM QA ainda nao foram aceitos.")


def _qa_key() -> str:
    preflight = verify_qa_origin()
    if (preflight.get("passed") is not True or preflight.get("production_contacted") is not False
            or preflight.get("origin") != QA_ORIGIN or preflight.get("project") != "nexpoint-erp-handoff-qa"):
        raise QAPublicationError("A origem isolada QA nao passou no preflight.")
    config = _inside(QA_ROOT / ".env.local", QA_ROOT)
    if not config.is_file() or config.stat().st_size > 4096:
        raise QAPublicationError("Configuracao privada QA ausente.")
    try:
        values = dict(line.split("=", 1) for line in config.read_text(encoding="utf-8").splitlines()
            if line and not line.startswith("#"))
        key = values["NEXPOINT_QA_SECRET_KEY"]
    except (OSError, UnicodeError, ValueError, KeyError):
        raise QAPublicationError("Configuracao privada QA invalida.") from None
    if not key.startswith("sb_secret_") or len(key) < 32 or any(c.isspace() for c in key):
        raise QAPublicationError("A chave do backend QA e invalida.")
    return key


_DB_SCOPE_SQL = """
select json_build_object(
  'database', current_database(),
  'migrations', (select count(*) from supabase_migrations.schema_migrations),
  'tenants', (select count(*) from public.np_tenants),
  'qa_tenants', (select count(*) from public.np_tenants where tenant_key='qa-nexpoint-lab' and status='active'),
  'qa_installations', (select count(*) from public.np_installations i join public.np_tenants t on t.id=i.tenant_id
    where t.tenant_key='qa-nexpoint-lab' and i.installation_key='qa-win-clean-04' and i.status='active'
      and i.update_channel='internal' and i.environment='prod' and i.channel='prod'),
  'private_bucket', (select count(*) from storage.buckets where id='nexpoint-releases'
    and public=false and file_size_limit=52428800),
  'active_admins', (select count(*) from public.np_platform_users where active and role='platform_admin'),
  'actor_id', (select id::text from public.np_platform_users where active and role='platform_admin' limit 1)
);
"""


def _qa_actor() -> str:
    try:
        result = subprocess.run(
            ["docker", "exec", "-i", QA_DB_CONTAINER, "psql", "-X", "-v", "ON_ERROR_STOP=1",
             "-U", "postgres", "-d", "postgres", "-Atq"],
            input=_DB_SCOPE_SQL, text=True, encoding="utf-8", capture_output=True, timeout=20,
        )
        if result.returncode or len(result.stdout) > 8192:
            raise ValueError
        scope = json.loads(result.stdout.strip())
        actor = str(UUID(scope["actor_id"]))
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired):
        raise QAPublicationError("O banco QA local nao passou no gate de identidade.") from None
    if (scope.get("database") != "postgres" or scope.get("migrations") != 9
            or scope.get("tenants") != 1 or scope.get("qa_tenants") != 1
            or scope.get("qa_installations") != 1 or scope.get("private_bucket") != 1
            or scope.get("active_admins") != 1):
        raise QAPublicationError("O banco QA local nao passou no gate de identidade.")
    return actor


def _qa_connection() -> http.client.HTTPConnection:
    return http.client.HTTPConnection(QA_LOOPBACK, QA_API_PORT, timeout=60)


def _qa_rpc(name: str, payload: dict, key: str) -> dict:
    if name not in {"np_admin_create_release", "np_admin_verify_release", "np_admin_publish_release"}:
        raise QAPublicationError("RPC de publicacao QA invalida.")
    connection = _qa_connection()
    try:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
        if len(body) > 32768:
            raise QAPublicationError("Payload de publicacao QA grande demais.")
        connection.request("POST", "/rest/v1/rpc/" + name, body,
            {"apikey": key, "Content-Type": "application/json", "Accept": "application/json"})
        response = connection.getresponse()
        raw = response.read(32769)
        if (response.status != 200 or len(raw) > 32768 or
                response.getheader("Content-Type", "").split(";", 1)[0] != "application/json"):
            raise QAPublicationError("A RPC local QA recusou a publicacao.")
        result = json.loads(raw)
        if not isinstance(result, dict) or result.get("ok") is not True:
            raise QAPublicationError("O catalogo local QA recusou a publicacao.")
        return result
    except QAPublicationError:
        raise
    except (OSError, UnicodeError, ValueError, http.client.HTTPException):
        raise QAPublicationError("A RPC local QA falhou.") from None
    finally:
        connection.close()


def publish_qa_release(artifact: Path, *, gate_report: Path | None = None, publish: bool = False) -> dict:
    artifact = _inside(artifact, QA_INSTALLERS)
    metadata = build_b_metadata(artifact)
    if not publish:
        return {"validated_locally": True, "published": False, "version": QA_VERSION,
                "update_channel": "internal", "artifact_sha256": metadata["artifact_sha256"]}
    if gate_report is None:
        raise QAPublicationError("O relatorio dos gates da Build A e obrigatorio.")
    require_build_a_gates(gate_report)
    key = _qa_key()
    actor = _qa_actor()
    created = _qa_rpc("np_admin_create_release", {"p_actor_id": actor, "p_metadata": metadata}, key)
    try:
        release_id = str(UUID(created["release_id"]))
    except (KeyError, TypeError, ValueError):
        raise QAPublicationError("O catalogo QA nao retornou um release valido.") from None
    storage = SupabaseReleaseStorage(QA_PROJECT_REF, key, connection_factory=_qa_connection)
    storage.upload_and_verify(artifact, metadata)
    _qa_rpc("np_admin_verify_release", {"p_actor_id": actor, "p_release_id": release_id,
        "p_sha256": metadata["artifact_sha256"], "p_size": metadata["artifact_size"]}, key)
    result = _qa_rpc("np_admin_publish_release", {"p_actor_id": actor, "p_release_id": release_id}, key)
    release = result.get("release")
    if (not isinstance(release, dict) or release.get("release_id") != release_id
            or release.get("version") != QA_VERSION or release.get("update_channel") != "internal"
            or release.get("artifact_sha256") != metadata["artifact_sha256"]):
        raise QAPublicationError("O catalogo QA retornou um manifesto inesperado.")
    return {"validated_locally": True, "published": True, "release_id": release_id,
            "version": QA_VERSION, "update_channel": "internal", "artifact_sha256": metadata["artifact_sha256"]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", required=True, type=Path)
    parser.add_argument("--gate-report", type=Path)
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = publish_qa_release(args.artifact, gate_report=args.gate_report, publish=args.publish)
    except Exception:
        print("Publicacao QA bloqueada: confira a Build B, o gate A e a origem QA isolada.", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
