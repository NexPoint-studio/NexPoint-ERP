"""Local read-only release preflight. Exit 0=PASS, 1=WARN, 2=FAIL.

The Doctor deliberately opens the observability database through SQLite's
``mode=ro`` URI. It never creates, migrates, prunes, checkpoints or repairs a
database while diagnosing it.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.config import get_settings  # noqa: E402
from app.core.database import build_engine  # noqa: E402
from app.observability.faults import (  # noqa: E402
    FaultInjectionContext,
    FaultInjectionDenied,
    FaultInjectionGuard,
)
from app.observability.sanitization import (  # noqa: E402
    REDACTED,
    contains_secret_canary,
    contains_secret_material,
)
from app.services.erp_preflight import run_preflight  # noqa: E402


_OBSERVABILITY_TABLE = "observability_events"
_OBSERVABILITY_REQUIRED_COLUMNS = frozenset({
    "event_id", "timestamp", "level", "environment", "tenant_id",
    "installation_id", "correlation_id", "module", "component",
    "event_type", "operation", "status", "fingerprint", "retry_count",
    "metadata_json", "schema_version", "sync_state",
})
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(?:password(?:[_-]?hash)?|passwd|passphrase|senha|secret|segredo|token|"
    r"reset[_-]?token|recovery[_-]?code|code[_-]?hash|api[_-]?key|"
    r"authorization|cookie|client[_-]?secret|service[_-]?role|hmac)"
    r"\b\s*[:=]\s*[\"']?([^\s,;}&\"']+)"
)
_AUTHORIZATION = re.compile(
    r"(?i)\b(?:authorization|proxy-authorization)\s*[:=]\s*"
    r"(?:bearer|basic)\s+(?!\[conteudo protegido\])\S+"
)
_PRIVATE_KEY = re.compile(r"-----BEGIN [^-]*PRIVATE KEY-----", re.IGNORECASE)
_JWT = re.compile(
    r"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]{8,}\."
    r"[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"
)
_SENSITIVE_KEY = re.compile(
    r"(?i)(?:password(?:[_-]?hash)?|passwd|passphrase|senha|secret|segredo|token|"
    r"reset[_-]?token|recovery[_-]?code(?:$|[_-](?:hash|value|plain(?:text)?))|"
    r"code[_-]?hash|api[_-]?key|"
    r"authorization|cookie|private[_-]?key|credential|service[_-]?role|hmac)"
)
_RECOVERY_CREDENTIAL = re.compile(
    r"(?i)\b(?:NXP-RESET-[A-Za-z0-9_-]{20,}|"
    r"NXP-[A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{4})\b"
)


def _finding(check: str, status: str, reason: str) -> dict[str, str]:
    return {"check": check, "status": status, "reason": reason}


def observability_database_path(operational_database: Path) -> Path:
    """Mirror the application's deterministic sidecar naming convention."""

    return operational_database.with_name(
        f"{operational_database.stem}_observability.sqlite3"
    )


def _readonly_sqlite(database_path: Path) -> sqlite3.Connection:
    """Open an existing SQLite database without permission to mutate it."""

    encoded = quote(database_path.resolve().as_posix(), safe="/:")
    connection = sqlite3.connect(
        f"file:{encoded}?mode=ro", uri=True, timeout=5, isolation_level=None
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA busy_timeout=5000")
    return connection


def _database_size(database_path: Path) -> int:
    """Count the database and WAL; SHM is coordination memory, not retained data."""

    return sum(
        candidate.stat().st_size
        for candidate in (database_path, Path(f"{database_path}-wal"))
        if candidate.is_file()
    )


def _contains_unredacted_secret(value: object) -> bool:
    """Detect high-confidence secret material without returning its contents."""

    rendered = str(value or "")
    if not rendered:
        return False
    if contains_secret_canary(rendered):
        return True
    if contains_secret_material(rendered):
        return True
    if _PRIVATE_KEY.search(rendered) or _JWT.search(rendered):
        return True
    if _RECOVERY_CREDENTIAL.search(rendered):
        return True
    if _AUTHORIZATION.search(rendered):
        return True
    try:
        structured = json.loads(rendered)
    except (TypeError, ValueError):
        structured = None

    def has_sensitive_value(item: object) -> bool:
        if isinstance(item, dict):
            for key, child in item.items():
                child_text = str(child or "").strip()
                if (_SENSITIVE_KEY.search(str(key)) and child_text
                        and child_text.casefold() != REDACTED.casefold()):
                    return True
                if has_sensitive_value(child):
                    return True
        elif isinstance(item, list):
            return any(has_sensitive_value(child) for child in item)
        return False

    if has_sensitive_value(structured):
        return True
    for match in _SECRET_ASSIGNMENT.finditer(rendered):
        candidate = match.group(1).strip().casefold()
        if (candidate and not candidate.startswith("[conteudo")
                and REDACTED.casefold() not in candidate):
            return True
    return False


def control_center_database_path(operational_database: Path) -> Path:
    source = operational_database.resolve()
    if source.name.casefold() == "erp.sqlite3":
        return source.with_name("control_center.sqlite3")
    suffix = source.suffix or ".sqlite3"
    return source.with_name(f"{source.stem}_control_center{suffix}")


def _table_names(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }


def inspect_operational_database(
    database_path: Path, *, now: datetime | None = None
) -> list[dict[str, str]]:
    """Inspect finance and recovery invariants without opening a write transaction."""

    if not database_path.is_file():
        return [_finding("operational_database", "FAIL", "Banco operacional não encontrado.")]
    instant = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    findings: list[dict[str, str]] = []
    try:
        connection = _readonly_sqlite(database_path)
    except Exception as exc:
        return [_finding(
            "operational_database", "FAIL",
            f"Falha ao abrir banco operacional em modo somente leitura ({type(exc).__name__}).",
        )]
    try:
        tables = _table_names(connection)
        required = {
            "payments", "cash_movements", "customer_receivables",
            "payment_allocations", "receivable_payments", "admin_locks",
            "admin_recovery_codes", "remember_sessions", "outbox_items", "audit_events",
        }
        missing = sorted(required - tables)
        findings.append(_finding(
            "operational_database", "FAIL" if missing else "PASS",
            "Tabelas obrigatórias ausentes: " + ", ".join(missing) + "."
            if missing else "Schema financeiro e de recuperação disponível em modo somente leitura.",
        ))
        integrity = [str(row[0]) for row in connection.execute("PRAGMA integrity_check")]
        foreign_keys = list(connection.execute("PRAGMA foreign_key_check"))
        findings.append(_finding(
            "operational_integrity",
            "PASS" if integrity == ["ok"] and not foreign_keys else "FAIL",
            "SQLite operacional íntegro e sem violações de chave estrangeira."
            if integrity == ["ok"] and not foreign_keys
            else "SQLite operacional falhou em integrity_check ou foreign_key_check.",
        ))
        if missing:
            return findings

        payment_link_errors = int(connection.execute("""
            SELECT count(*) FROM payments p
            WHERE p.status='CONFIRMED' AND (
                SELECT count(*) FROM cash_movements c
                WHERE c.source_type='PAYMENT' AND c.source_id=CAST(p.id AS TEXT)
                  AND c.status='ACTIVE'
            ) <> 1
        """).fetchone()[0])
        orphan_payment_cash = int(connection.execute("""
            SELECT count(*) FROM cash_movements c
            WHERE c.source_type='PAYMENT' AND c.status='ACTIVE'
              AND NOT EXISTS (
                SELECT 1 FROM payments p
                WHERE CAST(p.id AS TEXT)=c.source_id AND p.status='CONFIRMED'
              )
        """).fetchone()[0])
        receivable_cash_errors = int(connection.execute("""
            SELECT count(*) FROM receivable_payments rp
            WHERE (
                SELECT count(*) FROM cash_movements c
                WHERE c.source_type='RECEIVABLE_PAYMENT'
                  AND c.source_id=CAST(rp.id AS TEXT) AND c.status='ACTIVE'
            ) <> 1
        """).fetchone()[0])
        findings.append(_finding(
            "payment_cash_integrity",
            "FAIL" if payment_link_errors or orphan_payment_cash or receivable_cash_errors else "PASS",
            f"{payment_link_errors} pagamento(s) sem Caixa único, {orphan_payment_cash} "
            f"movimento(s) órfão(s) e {receivable_cash_errors} quitação(ões) sem Caixa único.",
        ))

        receivable_errors = int(connection.execute("""
            SELECT count(*) FROM customer_receivables r
            WHERE r.remaining_amount_cents < 0
               OR r.remaining_amount_cents > r.original_amount_cents
               OR (r.remaining_amount_cents = 0 AND r.status <> 'SETTLED')
               OR (r.remaining_amount_cents > 0 AND r.status = 'SETTLED')
               OR r.original_amount_cents - r.remaining_amount_cents <> (
                    COALESCE((SELECT sum(pa.amount_cents) FROM payment_allocations pa
                              WHERE pa.receivable_id=r.id), 0)
                    + COALESCE((SELECT sum(rp.amount_cents) FROM receivable_payments rp
                                WHERE rp.receivable_id=r.id), 0)
               )
        """).fetchone()[0])
        findings.append(_finding(
            "receivable_integrity", "FAIL" if receivable_errors else "PASS",
            f"{receivable_errors} saldo(s) devedor(es) divergem do ledger de alocações e quitações.",
        ))

        lock_rows = list(connection.execute(
            "SELECT id,password_hash,failed_attempt_count,recovery_failed_attempt_count "
            "FROM admin_locks"
        ))
        lock_invalid = len(lock_rows) > 1 or any(
            row[0] != 1 or not str(row[1]).startswith("scrypt$")
            or int(row[2]) < 0 or int(row[3]) < 0 for row in lock_rows
        )
        findings.append(_finding(
            "admin_lock_config", "FAIL" if lock_invalid else "PASS",
            "Cadeado administrativo ausente, pronto para configuração inicial."
            if not lock_rows else
            ("Configuração e contadores do cadeado são válidos."
             if not lock_invalid else "Configuração do cadeado administrativo é inválida."),
        ))
        legacy_active_codes = int(connection.execute(
            "SELECT count(*) FROM admin_recovery_codes WHERE status='ACTIVE'"
        ).fetchone()[0])
        remember_rows = list(connection.execute(
            "SELECT token_hash,installation_id,auth_version,session_generation_hash,"
            "status,expires_at,revoked_at FROM remember_sessions"
        ))
        remember_invalid = 0
        expired_active = 0
        for row in remember_rows:
            token_hash, installation_id, auth_version, generation_hash, status, expires_at, revoked_at = row
            lifecycle_ok = (
                status == "ACTIVE" and revoked_at is None
            ) or (
                status in {"REVOKED", "EXPIRED"} and revoked_at is not None
            )
            expires = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            if status == "ACTIVE" and expires <= instant:
                expired_active += 1
            if (
                not re.fullmatch(r"[0-9a-f]{64}", str(token_hash))
                or not re.fullmatch(r"[0-9a-f]{64}", str(generation_hash))
                or not str(installation_id)
                or int(auth_version) < 1
                or not lifecycle_ok
            ):
                remember_invalid += 1
        remember_status = (
            "FAIL" if remember_invalid or legacy_active_codes
            else "WARN" if expired_active
            else "PASS"
        )
        findings.append(_finding(
            "remember_session_store",
            remember_status,
            f"{len(remember_rows)} sessão(ões) persistente(s), {remember_invalid} inválida(s), "
            f"{expired_active} expirada(s) ainda ativa(s) e {legacy_active_codes} código(s) legado(s) ativo(s).",
        ))

        leaked_audits = sum(
            1 for row in connection.execute("SELECT action,resource,details FROM audit_events")
            if any(_contains_unredacted_secret(value) for value in row)
        )
        findings.append(_finding(
            "recovery_audit_secrets", "FAIL" if leaked_audits else "PASS",
            f"{leaked_audits} evento(s) de auditoria contêm material sensível não sanitizado.",
        ))

        stuck_cutoff = instant - timedelta(hours=24)
        stuck_requests = 0
        for row in connection.execute(
            "SELECT payload_json,created_at,status FROM outbox_items "
            "WHERE event_type='support_ticket' AND status IN ('pending','sending','failed')"
        ):
            try:
                payload = json.loads(str(row[0]))
                created = datetime.fromisoformat(str(row[1]).replace("Z", "+00:00"))
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                if payload.get("category") == "admin_access_recovery" and created < stuck_cutoff:
                    stuck_requests += 1
            except (TypeError, ValueError, json.JSONDecodeError):
                stuck_requests += 1
        findings.append(_finding(
            "recovery_request_stuck", "WARN" if stuck_requests else "PASS",
            f"{stuck_requests} solicitação(ões) de recuperação aguarda(m) sincronização há mais de 24 horas.",
        ))
    except Exception as exc:
        findings.append(_finding(
            "operational_inspection", "FAIL",
            f"Falha na inspeção operacional somente leitura ({type(exc).__name__}).",
        ))
    finally:
        connection.close()
    return findings


def inspect_control_center_recovery_database(
    database_path: Path, *, now: datetime | None = None
) -> list[dict[str, str]]:
    if not database_path.is_file():
        return [
            _finding("reset_authorization_store", "WARN", "Sidecar do Control Center ainda não existe."),
            _finding("reset_authorization_expired", "WARN", "Autorizações temporárias não puderam ser verificadas."),
        ]
    instant = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    try:
        connection = _readonly_sqlite(database_path)
    except Exception as exc:
        return [_finding(
            "reset_authorization_store", "FAIL",
            f"Falha ao abrir sidecar do Control Center em modo somente leitura ({type(exc).__name__}).",
        )]
    findings: list[dict[str, str]] = []
    try:
        tables = _table_names(connection)
        if "admin_reset_authorizations" not in tables:
            return [_finding(
                "reset_authorization_store", "WARN",
                "Schema de autorização temporária ainda não está presente.",
            )]
        invalid = int(connection.execute("""
            SELECT count(*) FROM admin_reset_authorizations
            WHERE token_hash NOT LIKE 'scrypt$%'
               OR status NOT IN ('active','consumed','expired','revoked')
               OR token_hash LIKE '%NXP-RESET-%'
        """).fetchone()[0])
        findings.append(_finding(
            "reset_authorization_store", "FAIL" if invalid else "PASS",
            f"{invalid} autorização(ões) possui(em) hash ou estado inválido.",
        ))
        active_expired = 0
        for row in connection.execute(
            "SELECT expires_at FROM admin_reset_authorizations WHERE status='active'"
        ):
            try:
                expires = datetime.fromisoformat(str(row[0]).replace("Z", "+00:00"))
                if expires.tzinfo is None:
                    expires = expires.replace(tzinfo=timezone.utc)
                active_expired += int(expires <= instant)
            except ValueError:
                active_expired += 1
        findings.append(_finding(
            "reset_authorization_expired", "WARN" if active_expired else "PASS",
            f"{active_expired} autorização(ões) ativa(s) já expirou(aram).",
        ))
        integrity = [str(row[0]) for row in connection.execute("PRAGMA integrity_check")]
        foreign_keys = list(connection.execute("PRAGMA foreign_key_check"))
        findings.append(_finding(
            "control_center_recovery_integrity",
            "PASS" if integrity == ["ok"] and not foreign_keys else "FAIL",
            "Sidecar de recuperação íntegro e referencialmente consistente."
            if integrity == ["ok"] and not foreign_keys
            else "Sidecar de recuperação falhou nas verificações SQLite.",
        ))
    except Exception as exc:
        findings.append(_finding(
            "control_center_recovery_inspection", "FAIL",
            f"Falha na inspeção do sidecar ({type(exc).__name__}).",
        ))
    finally:
        connection.close()
    return findings


def _secret_leak_count(connection: sqlite3.Connection, columns: set[str]) -> int:
    candidates = [
        column for column in (
            "user_pseudonym", "session_id", "correlation_id", "request_id",
            "module", "component", "event_type", "operation", "status",
            "error_code", "fingerprint", "metadata_json", "app_version", "build",
        )
        if column in columns
    ]
    if not candidates:
        return 0
    # Column names come only from the constant allowlist above.
    cursor = connection.execute(
        "SELECT " + ",".join(candidates) + f" FROM {_OBSERVABILITY_TABLE}"
    )
    leaked = 0
    for row in cursor:
        if any(_contains_unredacted_secret(row[column]) for column in candidates):
            leaked += 1
    return leaked


def inspect_observability_database(
    database_path: Path,
    settings: object,
    *,
    now: datetime | None = None,
) -> list[dict[str, str]]:
    """Inspect the log sidecar in read-only mode and return bounded findings."""

    if not database_path.is_file():
        return [
            _finding(
                "observability_database", "WARN",
                "Banco de observabilidade ainda não foi criado; nenhuma alteração foi feita.",
            ),
            _finding(
                "observability_secrets", "WARN",
                "Não foi possível verificar secrets sem o banco de observabilidade.",
            ),
        ]

    instant = now or datetime.now(timezone.utc)
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    instant = instant.astimezone(timezone.utc)
    retention_days = int(getattr(settings, "observability_retention_days", 30))
    max_events = int(getattr(settings, "observability_max_events", 50_000))
    max_bytes = int(getattr(settings, "observability_max_bytes", 50 * 1024 * 1024))
    findings: list[dict[str, str]] = []

    try:
        connection = _readonly_sqlite(database_path)
    except Exception as exc:
        return [_finding(
            "observability_database", "FAIL",
            f"Falha ao abrir banco de observabilidade em modo somente leitura ({type(exc).__name__}).",
        )]

    try:
        tables = {
            str(row[0]) for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if _OBSERVABILITY_TABLE not in tables:
            return [_finding(
                "observability_database", "FAIL",
                "Tabela de eventos de observabilidade ausente.",
            )]
        columns = {
            str(row[1]) for row in connection.execute(
                f"PRAGMA table_info({_OBSERVABILITY_TABLE})"
            )
        }
        missing_columns = sorted(_OBSERVABILITY_REQUIRED_COLUMNS - columns)
        findings.append(_finding(
            "observability_database", "FAIL" if missing_columns else "PASS",
            (
                "Colunas obrigatórias ausentes: " + ", ".join(missing_columns) + "."
                if missing_columns else
                "Banco separado de observabilidade disponível em modo somente leitura."
            ),
        ))

        integrity_rows = [str(row[0]) for row in connection.execute("PRAGMA integrity_check")]
        integrity_ok = integrity_rows == ["ok"]
        findings.append(_finding(
            "observability_integrity", "PASS" if integrity_ok else "FAIL",
            "SQLite de observabilidade íntegro." if integrity_ok
            else "SQLite de observabilidade falhou no integrity_check.",
        ))
        if missing_columns:
            findings.append(_finding(
                "observability_secrets", "WARN",
                "Verificação de secrets incompleta por incompatibilidade de schema.",
            ))
            return findings

        total = int(connection.execute(
            f"SELECT count(*) FROM {_OBSERVABILITY_TABLE}"
        ).fetchone()[0])
        cutoff = (instant - timedelta(days=retention_days)).isoformat(
            timespec="microseconds"
        ).replace("+00:00", "Z")
        stale_prunable = int(connection.execute(
            f"SELECT count(*) FROM {_OBSERVABILITY_TABLE} "
            "WHERE timestamp < ? AND sync_state IN ('local_only','synced')", (cutoff,),
        ).fetchone()[0])
        pending = int(connection.execute(
            f"SELECT count(*) FROM {_OBSERVABILITY_TABLE} WHERE sync_state='pending'"
        ).fetchone()[0])
        synced = int(connection.execute(
            f"SELECT count(*) FROM {_OBSERVABILITY_TABLE} WHERE sync_state='synced'"
        ).fetchone()[0])
        invalid_sync = int(connection.execute(
            f"SELECT count(*) FROM {_OBSERVABILITY_TABLE} "
            "WHERE sync_state NOT IN ('local_only','pending','synced') OR sync_state IS NULL"
        ).fetchone()[0])
        stuck_cutoff = (instant - timedelta(hours=24)).isoformat(
            timespec="microseconds"
        ).replace("+00:00", "Z")
        stuck = int(connection.execute(
            f"SELECT count(*) FROM {_OBSERVABILITY_TABLE} "
            "WHERE sync_state='pending' AND timestamp < ?", (stuck_cutoff,),
        ).fetchone()[0])

        retention_warn = bool(stale_prunable or total > max_events)
        findings.append(_finding(
            "observability_retention", "WARN" if retention_warn else "PASS",
            f"Retenção de {retention_days} dia(s): {stale_prunable} elegível(is) "
            f"para limpeza e {total} evento(s) no total.",
        ))
        size_bytes = _database_size(database_path)
        findings.append(_finding(
            "observability_size", "WARN" if size_bytes > max_bytes else "PASS",
            f"Uso em disco: {size_bytes} de {max_bytes} byte(s) permitidos.",
        ))
        findings.append(_finding(
            "observability_stuck", "WARN" if stuck else "PASS",
            f"{stuck} evento(s) pending há mais de 24 horas.",
        ))
        findings.append(_finding(
            "observability_sync",
            "FAIL" if invalid_sync else ("WARN" if stuck else "PASS"),
            f"Fila de logs: {pending} pending, {synced} sincronizado(s), "
            f"{invalid_sync} estado(s) inválido(s).",
        ))
        leak_count = _secret_leak_count(connection, columns)
        findings.append(_finding(
            "observability_secrets", "FAIL" if leak_count else "PASS",
            (
                f"{leak_count} evento(s) contêm material secreto não sanitizado."
                if leak_count else
                "Nenhum secret ou canary não sanitizado foi encontrado nos logs."
            ),
        ))
    except Exception as exc:
        findings.append(_finding(
            "observability_inspection", "FAIL",
            f"Falha na inspeção somente leitura ({type(exc).__name__}).",
        ))
    finally:
        connection.close()
    return findings


def qa_environment_findings(settings: object) -> list[dict[str, str]]:
    """Validate QA flags and execute the fail-closed fault-injection gate."""

    environment = str(getattr(settings, "environment", "local")).strip().casefold()
    tenant_type = str(getattr(settings, "tenant_type", "CUSTOMER")).strip().upper()
    qa_mode = bool(getattr(settings, "qa_mode", False))
    qa_valid = qa_mode and environment != "production" and tenant_type == "TEST"
    qa_invalid = qa_mode and not qa_valid
    if qa_valid:
        qa_reason = "Modo QA habilitado somente para tenant TEST fora de produção."
    elif qa_invalid:
        qa_reason = "Configuração QA inválida: exige tenant TEST fora de produção."
    elif tenant_type == "TEST":
        qa_reason = "Tenant TEST está com o modo QA desabilitado."
    else:
        qa_reason = "Modo QA desabilitado para este ambiente."
    findings = [_finding(
        "qa_environment",
        "FAIL" if qa_invalid else ("WARN" if tenant_type == "TEST" and not qa_mode else "PASS"),
        qa_reason,
    )]

    guard = FaultInjectionGuard(FaultInjectionContext(
        environment=environment, qa_mode=qa_mode,
        tenant_type=tenant_type, authorized=True,
    ))
    allowed = False
    try:
        guard.authorize("retry")
        allowed = True
    except FaultInjectionDenied:
        allowed = False
    except Exception as exc:
        findings.append(_finding(
            "fault_injection_guard", "FAIL",
            f"Falha ao avaliar o bloqueio de fault injection ({type(exc).__name__}).",
        ))
        return findings

    expected_allowed = qa_valid
    findings.append(_finding(
        "fault_injection_guard", "PASS" if allowed == expected_allowed else "FAIL",
        (
            "Fault injection restrito ao modo QA, tenant TEST e ambiente não produtivo."
            if allowed == expected_allowed else
            "Fault injection não respeitou as condições obrigatórias de QA."
        ),
    ))
    return findings


def main() -> int:
    findings: list[dict[str, str]] = []
    try:
        settings = get_settings()
        from sqlalchemy.engine import make_url

        database_file = Path(make_url(settings.database_url).database or "")
        if not database_file.is_file():
            raise FileNotFoundError("database_missing")
        engine = build_engine(settings.database_url)
        try:
            report = run_preflight(engine)
        finally:
            engine.dispose()
        findings.extend(
            dict(check=f.check, status=f.status, reason=f.reason)
            for f in report.findings
        )
        findings.extend(inspect_operational_database(database_file))
        findings.extend(inspect_control_center_recovery_database(
            control_center_database_path(database_file)
        ))
        findings.extend(inspect_observability_database(
            observability_database_path(database_file), settings
        ))
        findings.extend(qa_environment_findings(settings))
    except Exception as exc:
        findings.append(_finding(
            "configuration", "FAIL",
            f"Configuração inválida ({type(exc).__name__}).",
        ))
    if len(os.getenv("NEXA_ERP_BRIDGE_SECRET", "")) < 32:
        findings.append(_finding(
            "nexa_bridge", "WARN",
            "Ponte Nexa não configurada; o ERP continua independente.",
        ))
    changed = subprocess.run(
        ["git", "diff", "--name-only"], cwd=ROOT,
        capture_output=True, text=True, check=False,
    )
    if changed.returncode == 0:
        critical = (
            "app/main.py", "app/core/security.py", "app/migrations.py",
            "app/migration_definitions.py",
        )
        touched = sorted(set(changed.stdout.splitlines()) & set(critical))
        if touched:
            findings.append(_finding(
                "diff_risk", "WARN",
                "Arquivos críticos alterados: " + ", ".join(touched)
                + ". Execute a suíte completa.",
            ))
    status = (
        "FAIL" if any(item["status"] == "FAIL" for item in findings)
        else "WARN" if any(item["status"] == "WARN" for item in findings)
        else "PASS"
    )
    print(json.dumps({"status": status, "findings": findings}, ensure_ascii=False, indent=2))
    return {"PASS": 0, "WARN": 1, "FAIL": 2}[status]


if __name__ == "__main__":
    raise SystemExit(main())
