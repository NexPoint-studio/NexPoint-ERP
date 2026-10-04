"""Read-only inventory of an explicit operational SQLite COPY before handoff.

Does not open DPAPI, contact a service, authorize a move or repair a database.
Reports only fixed metadata, counts and booleans; no customer or secret values.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3


COUNT_TABLES = (
    "customers", "services", "service_notes", "service_note_items", "payments",
    "cash_movements", "customer_receivables", "receivable_payments",
    "note_closures", "payment_allocations", "users", "audit_events", "settings",
    "outbox_items",
)
STATUSES = frozenset({"pending", "sending", "synced", "failed", "dead_letter"})
PENDING_GATES = (
    "coordinated_writers_pause", "consistent_backup_of_all_stores",
    "dpapi_identity_verified_on_target_windows", "cloud_receipts_and_scope_verified",
    "sidecar_and_observability_queues_reconciled", "recovery_requests_reconciled",
    "handoff_implementation_and_crash_recovery_validated", "clean_windows_qa",
)


def inspect_copy(path: Path, *, expected_tenant: str, expected_installation: str) -> dict:
    result = {"schema_version": 1, "copy_checks_passed": False,
              "transfer_ready": False, "pending_gates": list(PENDING_GATES), "checks": {}}
    checks = result["checks"]
    if not expected_tenant.strip() or not expected_installation.strip():
        result["error"] = "expected_scope_required"
        return result
    try:
        path = Path(path)
        if path.is_symlink() or not path.is_file():
            result["error"] = "regular_copy_required"
            return result
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True,
                                     timeout=5)) as db:
            db.execute("pragma query_only=on")
            db.execute("pragma trusted_schema=off")
            db.execute("begin")
            # Diagnostic text from PRAGMAs can include row data. Emit booleans only.
            checks["integrity"] = db.execute("pragma integrity_check").fetchall() == [("ok",)]
            checks["foreign_keys"] = db.execute("pragma foreign_key_check").fetchone() is None
            if not all(checks.values()):
                return result
            tables = {r[0] for r in db.execute("select name from sqlite_master where type='table'")}
            checks["required_tables"] = set(COUNT_TABLES) <= tables
            if not checks["required_tables"]:
                result["missing_tables"] = sorted(set(COUNT_TABLES) - tables)
                return result
            # Names are an internal allowlist, never taken from user/schema strings.
            result["counts"] = {table: db.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
                                for table in COUNT_TABLES}
            scope = dict(db.execute("select key,value from settings where key in "
                "('system.control_center_identity','company.profile_tenant_id',"
                "'company.profile_installation_id','company.profile_revision')"))
            checks["durable_identity_present"] = bool(scope.get("system.control_center_identity"))
            checks["tenant_matches"] = scope.get("company.profile_tenant_id") == expected_tenant
            checks["installation_matches"] = scope.get("company.profile_installation_id") == expected_installation
            revision = scope.get("company.profile_revision", "")
            checks["profile_revision_valid"] = (revision.isascii() and revision.isdecimal()
                and len(revision) <= 16 and 1 <= int(revision) <= 9007199254740991)
            grouped = dict(db.execute("select status,count(*) from outbox_items group by status"))
            # Unknown states fail closed and are never echoed (could contain secrets).
            checks["known_outbox_states"] = set(grouped) <= STATUSES
            result["outbox_counts"] = {state: grouped.get(state, 0) for state in sorted(STATUSES)}
            checks["outbox_drained"] = all(state == "synced" for state in grouped)
            # 'synced' without a persisted ACK cannot prove that payload arrived.
            missing_acks = db.execute("select count(*) from outbox_items where status='synced' "
                "and (acked_at is null or remote_id is null or trim(remote_id)='')").fetchone()[0]
            result["outbox_missing_ack_count"] = missing_acks
            checks["synced_has_ack"] = missing_acks == 0
            result["copy_checks_passed"] = all(checks.values())
    except (sqlite3.Error, OSError, ValueError, TypeError, AttributeError):
        # Never leak database errors, paths or arbitrary stored values to a report.
        result["error"] = "copy_unreadable_or_schema_incompatible"
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="Inventariar copia SQLite para reassociacao; sem alterar dados")
    parser.add_argument("--operational-copy", type=Path, required=True)
    parser.add_argument("--expected-tenant", required=True)
    parser.add_argument("--expected-installation", required=True)
    args = parser.parse_args(argv)
    report = inspect_copy(args.operational_copy, expected_tenant=args.expected_tenant,
                          expected_installation=args.expected_installation)
    print(json.dumps(report, sort_keys=True))
    return 0 if report["copy_checks_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
