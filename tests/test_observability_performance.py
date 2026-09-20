from __future__ import annotations

import sqlite3
from time import perf_counter

from app.observability import ObservabilityStore, build_observability_event


def _business_database(path):
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute(
        "CREATE TABLE operations(id INTEGER PRIMARY KEY, status TEXT NOT NULL)"
    )
    return connection


def _run_actions(connection, count: int, store: ObservabilityStore | None = None):
    started = perf_counter()
    for index in range(count):
        with connection:
            connection.execute(
                "INSERT INTO operations(id,status) VALUES(?,?)",
                (index + 1, "completed"),
            )
        if store is not None:
            store.append(build_observability_event(
                event_id=f"performance_{index:04d}",
                level="INFO",
                environment="test",
                tenant_id="tenant_performance_test",
                installation_id="installation_performance_test",
                module="qa",
                component="performance",
                event_type="qa.performance.sample",
                operation="insert",
                status="completed",
                sync_state="local_only",
            ))
    return perf_counter() - started


def test_observability_overhead_remains_bounded_for_normal_local_actions(
    tmp_path,
):
    count = 200
    baseline = _business_database(tmp_path / "baseline.sqlite3")
    instrumented = _business_database(tmp_path / "instrumented.sqlite3")
    store = ObservabilityStore(tmp_path / "performance_observability.sqlite3")
    try:
        baseline_seconds = _run_actions(baseline, count)
        instrumented_seconds = _run_actions(instrumented, count, store)
    finally:
        baseline.close()
        instrumented.close()

    overhead_ms = max(0.0, instrumented_seconds - baseline_seconds) * 1000 / count
    print(
        "observability_performance "
        f"actions={count} baseline_ms={baseline_seconds * 1000:.2f} "
        f"instrumented_ms={instrumented_seconds * 1000:.2f} "
        f"overhead_ms_per_action={overhead_ms:.3f}"
    )
    assert store.diagnostics()["integrity"] == "ok"
    assert len(store.list_events(limit=count)) == count
    assert overhead_ms < 20.0
