"""Durable SQLite store physically separate from the operational ERP database."""
from __future__ import annotations

from contextlib import closing, contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sqlite3
from typing import Iterator

from app.observability.events import (
    OBSERVABILITY_LEVELS,
    ObservabilityEvent,
    ObservabilityFilters,
    ObservabilityRetention,
    as_utc,
)
from app.observability.sanitization import metadata_dict, sanitize_text


SCHEMA_VERSION = "1"

_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS observability_meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS observability_events (
        event_id TEXT PRIMARY KEY,
        timestamp TEXT NOT NULL,
        level TEXT NOT NULL CHECK(level IN ('DEBUG','INFO','WARNING','ERROR','CRITICAL')),
        environment TEXT NOT NULL,
        tenant_id TEXT NOT NULL,
        installation_id TEXT NOT NULL,
        user_pseudonym TEXT,
        session_id TEXT,
        correlation_id TEXT,
        request_id TEXT,
        module TEXT NOT NULL,
        component TEXT NOT NULL,
        event_type TEXT NOT NULL,
        operation TEXT NOT NULL,
        status TEXT NOT NULL,
        duration_ms INTEGER CHECK(duration_ms IS NULL OR duration_ms BETWEEN 0 AND 3600000),
        error_code TEXT,
        fingerprint TEXT NOT NULL,
        retry_count INTEGER NOT NULL CHECK(retry_count BETWEEN 0 AND 1000),
        metadata_json TEXT NOT NULL DEFAULT '{}',
        schema_version INTEGER NOT NULL CHECK(schema_version BETWEEN 1 AND 100),
        app_version TEXT,
        build TEXT,
        sync_state TEXT NOT NULL CHECK(sync_state IN ('local_only','pending','synced'))
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_observability_time ON observability_events(timestamp DESC)",
    "CREATE INDEX IF NOT EXISTS ix_observability_correlation ON observability_events(correlation_id,timestamp,event_id)",
    "CREATE INDEX IF NOT EXISTS ix_observability_fingerprint ON observability_events(fingerprint,timestamp DESC)",
    "CREATE INDEX IF NOT EXISTS ix_observability_filters ON observability_events(level,module,component,event_type,status,timestamp DESC)",
    "CREATE INDEX IF NOT EXISTS ix_observability_sync ON observability_events(sync_state,timestamp)",
)


def _iso(value: datetime) -> str:
    return as_utc(value).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _datetime(raw: str) -> datetime:
    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    return as_utc(parsed)


class ObservabilityStore:
    """Small fail-isolated event store; callers decide whether failures matter."""

    def __init__(
        self,
        database_path: str | Path,
        *,
        retention: ObservabilityRetention | None = None,
        operational_database_path: str | Path | None = None,
    ) -> None:
        self.database_path = Path(database_path).expanduser().resolve()
        operational = (
            Path(operational_database_path).expanduser().resolve()
            if operational_database_path is not None else None
        )
        same_database = operational is not None and self.database_path == operational
        if (
            not same_database
            and operational is not None
            and operational.exists()
            and self.database_path.exists()
        ):
            try:
                same_database = os.path.samefile(self.database_path, operational)
            except OSError:
                same_database = False
        if same_database:
            raise ValueError("A observabilidade não pode usar o banco operacional.")
        if self.database_path.suffix.casefold() not in {".sqlite", ".sqlite3", ".db"}:
            raise ValueError("O armazenamento de observabilidade deve ser SQLite.")
        self.retention = retention or ObservabilityRetention()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def _connect(self, *, configure_journal: bool = False) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=5, isolation_level=None)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=5000")
            # journal_mode is persistent database configuration. Reapplying it
            # for every event can request an avoidable lock transition on Windows.
            if configure_journal:
                applied = connection.execute("PRAGMA journal_mode=WAL").fetchone()
                if applied is None or str(applied[0]).casefold() != "wal":
                    raise sqlite3.OperationalError(
                        "observability_wal_unavailable"
                    )
            connection.execute("PRAGMA synchronous=NORMAL")
            return connection
        except Exception:
            connection.close()
            raise

    @contextmanager
    def _write(
        self, *, configure_journal: bool = False
    ) -> Iterator[sqlite3.Connection]:
        connection = self._connect(configure_journal=configure_journal)
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @contextmanager
    def _read(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("PRAGMA query_only=ON")
            yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        with self._write(configure_journal=True) as connection:
            for statement in _SCHEMA:
                connection.execute(statement)
            connection.execute(
                "INSERT INTO observability_meta(key,value) VALUES('schema_version',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (SCHEMA_VERSION,),
            )

    @staticmethod
    def _from_row(row: sqlite3.Row) -> ObservabilityEvent:
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
        except (TypeError, ValueError):
            metadata = {}
        return ObservabilityEvent(
            event_id=row["event_id"], timestamp=_datetime(row["timestamp"]),
            level=row["level"], environment=row["environment"],
            tenant_id=row["tenant_id"], installation_id=row["installation_id"],
            user_pseudonym=row["user_pseudonym"], session_id=row["session_id"],
            correlation_id=row["correlation_id"], request_id=row["request_id"],
            module=row["module"], component=row["component"],
            event_type=row["event_type"], operation=row["operation"], status=row["status"],
            duration_ms=row["duration_ms"], error_code=row["error_code"],
            fingerprint=row["fingerprint"], retry_count=int(row["retry_count"]),
            metadata=metadata_dict(metadata if isinstance(metadata, dict) else {}),
            schema_version=int(row["schema_version"]), app_version=row["app_version"],
            build=row["build"], sync_state=row["sync_state"],
        )

    def append(self, event: ObservabilityEvent) -> ObservabilityEvent:
        metadata_json = json.dumps(
            metadata_dict(event.metadata), ensure_ascii=True, sort_keys=True,
            separators=(",", ":"),
        )
        with self._write() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO observability_events(
                    event_id,timestamp,level,environment,tenant_id,installation_id,
                    user_pseudonym,session_id,correlation_id,request_id,module,component,
                    event_type,operation,status,duration_ms,error_code,fingerprint,retry_count,
                    metadata_json,schema_version,app_version,build,sync_state
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    event.event_id, _iso(event.timestamp), event.level, event.environment,
                    event.tenant_id, event.installation_id, event.user_pseudonym,
                    event.session_id, event.correlation_id, event.request_id,
                    event.module, event.component, event.event_type, event.operation,
                    event.status, event.duration_ms, event.error_code, event.fingerprint,
                    event.retry_count, metadata_json, event.schema_version,
                    event.app_version, event.build, event.sync_state,
                ),
            )
        return event

    def get(self, event_id: str) -> ObservabilityEvent | None:
        with self._read() as connection:
            row = connection.execute(
                "SELECT * FROM observability_events WHERE event_id=?", (str(event_id),)
            ).fetchone()
        return self._from_row(row) if row else None

    def list_events(
        self, filters: ObservabilityFilters | None = None, *, limit: int = 200,
        offset: int = 0,
    ) -> tuple[ObservabilityEvent, ...]:
        selected = filters or ObservabilityFilters()
        clauses: list[str] = []
        values: list[object] = []
        for field, value in (
            ("tenant_id", selected.tenant_id), ("installation_id", selected.installation_id),
            ("module", selected.module), ("component", selected.component),
            ("event_type", selected.event_type), ("status", selected.status),
            ("fingerprint", selected.fingerprint), ("correlation_id", selected.correlation_id),
            ("error_code", selected.error_code), ("operation", selected.operation),
        ):
            if value:
                clauses.append(f"{field}=?")
                values.append(str(value))
        if selected.started_at:
            clauses.append("timestamp>=?")
            values.append(_iso(selected.started_at))
        if selected.ended_at:
            clauses.append("timestamp<=?")
            values.append(_iso(selected.ended_at))
        levels = tuple(level for level in selected.levels if level in OBSERVABILITY_LEVELS)
        if levels:
            clauses.append("level IN (" + ",".join("?" for _ in levels) + ")")
            values.extend(levels)
        if selected.query:
            query = sanitize_text(selected.query, maximum=120)
            if query:
                clauses.append(
                    "(correlation_id LIKE ? ESCAPE '\\' OR fingerprint LIKE ? ESCAPE '\\' "
                    "OR error_code LIKE ? ESCAPE '\\' OR operation LIKE ? ESCAPE '\\')"
                )
                escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                values.extend([f"%{escaped}%"] * 4)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        bounded_limit = max(1, min(int(limit), 1000))
        bounded_offset = max(0, min(int(offset), 1_000_000))
        with self._read() as connection:
            rows = connection.execute(
                f"SELECT * FROM observability_events{where} "
                "ORDER BY timestamp DESC,event_id DESC LIMIT ? OFFSET ?",
                (*values, bounded_limit, bounded_offset),
            ).fetchall()
        return tuple(self._from_row(row) for row in rows)

    def timeline(self, correlation_id: str, *, limit: int = 500) -> tuple[ObservabilityEvent, ...]:
        events = self.list_events(
            ObservabilityFilters(correlation_id=str(correlation_id)), limit=limit
        )
        return tuple(sorted(events, key=lambda item: (item.timestamp, item.event_id)))

    def pending_events(self, *, limit: int = 100) -> tuple[ObservabilityEvent, ...]:
        """Return the oldest unsynchronized events first to prevent starvation."""

        bounded = max(1, min(int(limit), 1000))
        with self._read() as connection:
            rows = connection.execute(
                "SELECT * FROM observability_events WHERE sync_state='pending' "
                "ORDER BY timestamp,event_id LIMIT ?",
                (bounded,),
            ).fetchall()
        return tuple(self._from_row(row) for row in rows)

    def mark_pending(self, event_id: str) -> bool:
        return self._set_sync_state(event_id, "pending", from_states=("local_only", "pending"))

    def mark_synced(self, event_id: str) -> bool:
        return self._set_sync_state(event_id, "synced", from_states=("pending", "synced"))

    def _set_sync_state(self, event_id: str, state: str, *, from_states: tuple[str, ...]) -> bool:
        with self._write() as connection:
            result = connection.execute(
                "UPDATE observability_events SET sync_state=? WHERE event_id=? AND sync_state IN ("
                + ",".join("?" for _ in from_states) + ")",
                (state, str(event_id), *from_states),
            )
            return bool(result.rowcount)

    def retention_cleanup(self, *, now: datetime | None = None) -> dict[str, int]:
        """Prune only local-only or ACKed rows; pending rows are always protected."""

        instant = as_utc(now)
        cutoff = _iso(instant - timedelta(days=self.retention.days))
        deleted_age = deleted_count = deleted_size = 0
        with self._write() as connection:
            result = connection.execute(
                "DELETE FROM observability_events WHERE timestamp<? "
                "AND sync_state IN ('local_only','synced')",
                (cutoff,),
            )
            deleted_age = int(result.rowcount or 0)
            total = int(connection.execute("SELECT COUNT(*) FROM observability_events").fetchone()[0])
            excess = max(0, total - self.retention.max_events)
            if excess:
                result = connection.execute(
                    "DELETE FROM observability_events WHERE event_id IN ("
                    "SELECT event_id FROM observability_events "
                    "WHERE sync_state IN ('local_only','synced') "
                    "ORDER BY timestamp,event_id LIMIT ?)",
                    (excess,),
                )
                deleted_count = int(result.rowcount or 0)
        # SQLite reuses free pages. Compact only during explicit maintenance and
        # only if size is over budget; this store is independent from operations.
        while (self.database_path.exists()
               and self.database_path.stat().st_size > self.retention.max_bytes):
            removed = 0
            with self._write() as connection:
                    ids = connection.execute(
                        "SELECT event_id FROM observability_events "
                        "WHERE sync_state IN ('local_only','synced') "
                        "ORDER BY timestamp,event_id LIMIT 1000"
                    ).fetchall()
                    if ids:
                        connection.executemany(
                            "DELETE FROM observability_events WHERE event_id=?",
                            [(row[0],) for row in ids],
                        )
                        removed = len(ids)
                        deleted_size += removed
            if not removed:
                # Pending rows are deliberately retained even if they alone exceed
                # the configured disk budget.
                break
            connection = self._connect()
            try:
                connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                connection.execute("VACUUM")
            finally:
                connection.close()
        return {"age": deleted_age, "count": deleted_count, "size": deleted_size}

    def diagnostics(self, *, now: datetime | None = None) -> dict[str, object]:
        instant = as_utc(now)
        with self._read() as connection:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            counts = {
                row[0]: int(row[1])
                for row in connection.execute(
                    "SELECT sync_state,COUNT(*) FROM observability_events GROUP BY sync_state"
                ).fetchall()
            }
            stuck = int(connection.execute(
                "SELECT COUNT(*) FROM observability_events WHERE sync_state='pending' AND timestamp<?",
                (_iso(instant - timedelta(hours=24)),),
            ).fetchone()[0])
        return {
            "integrity": integrity,
            "size_bytes": self.database_path.stat().st_size if self.database_path.exists() else 0,
            "counts": counts,
            "stuck_pending": stuck,
            "retention_days": self.retention.days,
            "max_events": self.retention.max_events,
            "max_bytes": self.retention.max_bytes,
        }

    def close(self) -> None:
        """Connections are short-lived; kept for a symmetric application API."""
