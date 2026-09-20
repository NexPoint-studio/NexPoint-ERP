"""Read-only, deterministic ERP doctor suitable for local release checks."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.engine import Engine

from app.migrations import LATEST_SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class PreflightFinding:
    check: str
    status: str
    reason: str


@dataclass(frozen=True, slots=True)
class PreflightReport:
    status: str
    findings: tuple[PreflightFinding, ...]
    generated_at: datetime


_TABLES = frozenset({
    "users", "roles", "permissions", "customers", "services", "service_notes",
    "payments", "cash_movements", "outbox_items", "diagnostic_events",
    "nonce_receipts", "admin_locks", "note_closures", "customer_receivables",
    "note_receivable_links", "payment_allocations", "receivable_payments",
})


def _count(connection, statement: str, parameters: tuple = ()) -> int:
    """Only call with fixed SQL statements defined in this module."""
    return int(connection.exec_driver_sql(statement, parameters).scalar_one())


def _local_domain_findings(connection, tables: frozenset[str], now: datetime) -> list[PreflightFinding]:
    findings: list[PreflightFinding] = []
    if "outbox_items" in tables:
        obsolete = _count(connection,
            "SELECT count(*) FROM outbox_items WHERE status = 'sending' "
            "AND (lease_until IS NULL OR lease_until <= ?)",
            (now.replace(tzinfo=None).isoformat(sep=" "),))
        dead = _count(connection, "SELECT count(*) FROM outbox_items WHERE status = 'dead_letter'")
        bad_payload = _count(connection,
            "SELECT count(*) FROM outbox_items WHERE schema_version <> 1 OR "
            "payload_json IS NULL OR json_valid(payload_json) <> 1")
        findings.extend((
            PreflightFinding("outbox_stuck", "WARN" if obsolete else "PASS",
                             f"{obsolete} envio(s) com lease vencido."),
            PreflightFinding("outbox_dead_letter", "WARN" if dead else "PASS",
                             f"{dead} item(ns) em dead letter."),
            PreflightFinding("payload_versions", "FAIL" if bad_payload else "PASS",
                             f"{bad_payload} payload(s) inválidos ou com versão incompatível."),
        ))
    for table, label in (("nonce_receipts", "nonce_store"),
                         ("diagnostic_events", "telemetry_store")):
        findings.append(PreflightFinding(label, "PASS" if table in tables else "FAIL",
                                         "Persistência disponível." if table in tables else "Tabela ausente."))
    if {"payments", "payment_allocations", "service_notes"} <= tables:
        wrong_allocations = _count(connection,
            "SELECT count(*) FROM payments p WHERE p.status = 'CONFIRMED' AND "
            "(SELECT coalesce(sum(a.amount_cents), 0) FROM payment_allocations a "
            "WHERE a.payment_id = p.id) NOT IN (0, p.gross_amount_cents)")
        overpaid = _count(connection,
            "SELECT count(*) FROM service_notes n WHERE "
            "(SELECT coalesce(sum(CASE WHEN EXISTS(SELECT 1 FROM payment_allocations a "
            "WHERE a.payment_id = p.id) THEN coalesce((SELECT sum(a.amount_cents) "
            "FROM payment_allocations a WHERE a.payment_id = p.id AND a.receivable_id IS NULL), 0) "
            "ELSE p.gross_amount_cents END), 0) FROM payments p "
            "WHERE p.service_note_id = n.id AND p.status = 'CONFIRMED') > n.total_cents")
        findings.extend((
            PreflightFinding("payment_allocations", "FAIL" if wrong_allocations else "PASS",
                             f"{wrong_allocations} pagamento(s) com alocação divergente."),
            PreflightFinding("payment_limits", "FAIL" if overpaid else "PASS",
                             f"{overpaid} Nota(s) com pagamentos acima do valor atual."),
        ))
    if {"customer_receivables", "service_notes", "note_closures",
        "receivable_payments", "payment_allocations", "payments"} <= tables:
        wrong_receivables = _count(connection,
            "SELECT count(*) FROM customer_receivables r JOIN service_notes n "
            "ON n.id = r.source_note_id LEFT JOIN note_closures c "
            "ON c.service_note_id = r.source_note_id WHERE r.customer_id <> n.customer_id "
            "OR c.id IS NULL OR r.original_amount_cents <> c.outstanding_amount_cents "
            "OR (r.status = 'SETTLED') <> (r.remaining_amount_cents = 0) "
            "OR r.remaining_amount_cents + coalesce((SELECT sum(d.amount_cents) "
            "FROM receivable_payments d WHERE d.receivable_id = r.id), 0) + "
            "coalesce((SELECT sum(a.amount_cents) FROM payment_allocations a "
            "JOIN payments p ON p.id = a.payment_id WHERE a.receivable_id = r.id "
            "AND p.status = 'CONFIRMED'), 0) <> r.original_amount_cents")
        wrong_closures = _count(connection,
            "SELECT count(*) FROM note_closures c JOIN service_notes n ON n.id = c.service_note_id "
            "WHERE n.operational_status <> 'FECHADO' OR n.total_cents <> c.total_amount_cents "
            "OR c.paid_amount_cents + c.outstanding_amount_cents <> c.total_amount_cents "
            "OR (c.outstanding_amount_cents > 0 AND NOT EXISTS "
            "(SELECT 1 FROM customer_receivables r WHERE r.source_note_id = n.id))")
        findings.extend((
            PreflightFinding("receivables_integrity", "FAIL" if wrong_receivables else "PASS",
                             f"{wrong_receivables} saldo(s) inconsistentes."),
            PreflightFinding("closures_integrity", "FAIL" if wrong_closures else "PASS",
                             f"{wrong_closures} fechamento(s) inconsistentes."),
        ))
    if {"payments", "cash_movements"} <= tables:
        missing_cash = _count(connection,
            "SELECT count(*) FROM payments p LEFT JOIN cash_movements c "
            "ON c.source_type = 'PAYMENT' AND c.source_id = CAST(p.id AS TEXT) "
            "AND c.status = 'ACTIVE' WHERE p.status = 'CONFIRMED' AND "
            "(c.id IS NULL OR round(c.gross_amount * 100) <> p.gross_amount_cents)")
        findings.append(PreflightFinding("payment_cash", "FAIL" if missing_cash else "PASS",
                                         f"{missing_cash} pagamento(s) sem entrada de Caixa equivalente."))
    if {"receivable_payments", "cash_movements"} <= tables:
        missing_cash = _count(connection,
            "SELECT count(*) FROM receivable_payments p LEFT JOIN cash_movements c "
            "ON c.source_type = 'RECEIVABLE_PAYMENT' AND c.source_id = CAST(p.id AS TEXT) "
            "AND c.status = 'ACTIVE' WHERE c.id IS NULL OR "
            "round(c.gross_amount * 100) <> p.amount_cents")
        findings.append(PreflightFinding("receivable_cash", "FAIL" if missing_cash else "PASS",
                                         f"{missing_cash} recebimento(s) de saldo sem Caixa equivalente."))
    return findings


def run_preflight(engine: Engine, *, expected_schema: str = LATEST_SCHEMA_VERSION,
                  now: datetime | None = None) -> PreflightReport:
    """Checks schema and SQLite integrity without migrations or data mutation."""
    findings = []
    instant = now or datetime.now(timezone.utc)
    try:
        with engine.connect() as connection:
            tables = frozenset(row[0] for row in connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ))
            missing = sorted(_TABLES - tables)
            findings.append(PreflightFinding(
                "required_tables", "FAIL" if missing else "PASS",
                "Tabelas ausentes: " + ", ".join(missing) if missing else "Tabelas essenciais presentes.",
            ))
            if "schema_migrations" not in tables:
                findings.append(PreflightFinding("schema", "FAIL", "Registro de migrations ausente."))
            else:
                versions = {str(row[0]) for row in connection.exec_driver_sql("SELECT version FROM schema_migrations")}
                findings.append(PreflightFinding(
                    "schema", "PASS" if expected_schema in versions else "FAIL",
                    f"Migration esperada {expected_schema}: " + ("aplicada." if expected_schema in versions else "ausente."),
                ))
            integrity = connection.exec_driver_sql("PRAGMA quick_check").scalar()
            findings.append(PreflightFinding(
                "sqlite_integrity", "PASS" if integrity == "ok" else "FAIL",
                "SQLite íntegro." if integrity == "ok" else "SQLite quick_check falhou.",
            ))
            foreign_keys = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchmany(1)
            findings.append(PreflightFinding(
                "foreign_keys", "FAIL" if foreign_keys else "PASS",
                "Há violação de chave estrangeira." if foreign_keys else "Sem violações de chave estrangeira.",
            ))
            findings.extend(_local_domain_findings(connection, tables, instant))
    except Exception as exc:
        # No SQL, paths or exception message in report.
        findings.append(PreflightFinding("database_connection", "FAIL", f"Falha de acesso ao SQLite ({type(exc).__name__})."))
    status = "FAIL" if any(f.status == "FAIL" for f in findings) else (
        "WARN" if any(f.status == "WARN" for f in findings) else "PASS"
    )
    return PreflightReport(status, tuple(findings), instant)
