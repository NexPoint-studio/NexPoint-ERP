"""Cópia portátil, crash controlado e quatro ciclos reais de pywebview."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import os
import secrets
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from contextlib import closing

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts"
sys.path.insert(0, str(ROOT))

from app.migrations import SUPPORTED_SCHEMA_VERSIONS
from app import create_app
from control_center.domain import PlatformUser


def remove_temporary_copy(target: Path) -> bool:
    """Aguarda o WebView2 liberar arquivos da cópia e remove só esse diretório."""
    resolved = target.resolve()
    artifacts = ARTIFACTS.resolve()
    if resolved.parent != artifacts or not resolved.name.startswith("desktop_copy_"):
        raise RuntimeError("Diretório temporário inválido")
    for _ in range(20):
        try:
            shutil.rmtree(resolved)
            return True
        except FileNotFoundError:
            return True
        except PermissionError:
            time.sleep(0.25)
    return not resolved.exists()


def authorize_disposable_admin_reset(target: Path, environment: dict[str, str]) -> dict[str, object]:
    """Sincroniza e autoriza somente o chamado criado na cópia descartável."""

    database = target / "data" / "erp.sqlite3"
    control_database = target / "data" / "control_center.sqlite3"
    application = create_app(
        database_url=f"sqlite+pysqlite:///{database.as_posix()}",
        credentials={},
        session_secret=environment["ERP_SESSION_SECRET"],
        restore_enabled=False,
        control_center_database_path=control_database,
    )
    try:
        sync_result = application.state.sync_engine.run_once()
        repository = application.state.control_center_repository
        if repository is None:
            raise RuntimeError("Control Center descartável indisponível")
        with closing(sqlite3.connect(control_database)) as connection:
            row = connection.execute(
                "SELECT id FROM tickets WHERE category='admin_access_recovery' "
                "ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        if row is None:
            raise RuntimeError("Chamado de recuperação descartável não sincronizado")
        admin = repository.save_platform_user(
            PlatformUser(
                id="platform_admin_desktop_validation",
                username="desktop-validation@nexpoint.local",
                display_name="Admin descartável do desktop",
                role="platform_admin",
            ),
            password="CANARY-Platform-Desktop-2026",
        )
        authorization = repository.authorize_admin_reset(
            str(row[0]), authorized_by=admin.id, lifetime_minutes=15
        )
        return {
            "ticket_synced": True,
            "authorization_created": authorization.status == "active",
            "synced_items": sync_result.synced,
        }
    finally:
        store = getattr(application.state, "observability_store", None)
        if store is not None:
            store.close()
        repository = getattr(application.state, "control_center_repository", None)
        if repository is not None:
            repository.close()
        application.state.engine.dispose()


def recover_and_drain_disposable_outbox(
    target: Path, environment: dict[str, str]
) -> dict[str, object]:
    """Recupera leases do crash e prova que a fila descartável volta a zero."""

    database = target / "data" / "erp.sqlite3"
    control_database = target / "data" / "control_center.sqlite3"
    application = create_app(
        database_url=f"sqlite+pysqlite:///{database.as_posix()}",
        credentials={},
        session_secret=environment["ERP_SESSION_SECRET"],
        restore_enabled=False,
        control_center_database_path=control_database,
    )
    recovered = synced = dead_letter = failed = 0
    runs = 0
    try:
        instant = datetime.now(timezone.utc) + timedelta(minutes=2)
        recovered = application.state.sync_engine.recover(now=instant)
        for offset in range(20):
            result = application.state.sync_engine.run_once(
                now=instant + timedelta(seconds=offset + 1)
            )
            runs += 1
            synced += result.synced
            dead_letter += result.dead_letter
            failed += result.failed
            if result.claimed == 0:
                break
        with closing(sqlite3.connect(database)) as connection:
            unfinished = int(connection.execute(
                "SELECT count(*) FROM outbox_items "
                "WHERE status IN ('pending','sending','failed','dead_letter')"
            ).fetchone()[0])
        assert dead_letter == 0 and failed == 0 and unfinished == 0, (
            recovered,
            synced,
            failed,
            dead_letter,
            unfinished,
        )
        return {
            "recovered_leases": recovered,
            "synced_items": synced,
            "runs": runs,
            "unfinished_items": unfinished,
        }
    finally:
        store = getattr(application.state, "observability_store", None)
        if store is not None:
            store.close()
        repository = getattr(application.state, "control_center_repository", None)
        if repository is not None:
            repository.close()
        application.state.engine.dispose()


def main():
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="desktop_copy_", dir=ARTIFACTS, ignore_cleanup_errors=True) as temporary:
        target = Path(temporary)
        for name in ("app", "templates", "static", "control_center"):
            shutil.copytree(ROOT / name, target / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        for name in (
            "run_desktop.py",
            "run_dev.py",
            "pyproject.toml",
            "sanitization_contract.py",
        ):
            shutil.copy2(ROOT / name, target / name)
        shutil.copy2(ROOT / "scripts" / "desktop_validation_child.py", target / "validation_child.py")
        (target / "scripts").mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / "scripts" / "erp_doctor.py", target / "scripts" / "erp_doctor.py")
        assert not (target / ".env.local").exists() and not (target / "data").exists()
        environment = {key: value for key, value in os.environ.items() if not key.startswith("ERP_")}
        environment.update(ERP_SESSION_SECRET=secrets.token_urlsafe(40), ERP_ADMIN_PASSWORD="senha-local-exclusiva-de-validacao", ERP_HOST="127.0.0.1", ERP_PORT="8877", PYTHONUTF8="1")
        results = {"copy_without_operational_data": True, "cycles": []}
        crash = subprocess.Popen([sys.executable, str(target / "validation_child.py"), "crash"], cwd=target, env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 25
            while not (target / "crash-ready.json").exists():
                if crash.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError("O processo de crash não iniciou")
                time.sleep(0.1)
            crash.kill()  # Somente este filho descartável, nunca processo normal.
            crash.communicate(timeout=10)
        finally:
            if crash.poll() is None:
                crash.kill()
                crash.communicate(timeout=10)
        for cycle in range(1, 5):
            completed = subprocess.run([sys.executable, str(target / "validation_child.py"), str(cycle)], cwd=target, env=environment, capture_output=True, text=True, encoding="utf-8", timeout=55)
            evidence_path = target / f"desktop-{cycle}.json"
            evidence = json.loads(evidence_path.read_text(encoding="utf-8")) if evidence_path.exists() else {}
            results["cycles"].append(evidence)
            assert completed.returncode == 0 and evidence.get("completed"), (cycle, evidence, completed.stderr[-1500:])
            with socket.socket() as probe:
                assert probe.connect_ex(("127.0.0.1", 8877)) != 0, "Servidor órfão na porta 8877"
            if cycle == 2:
                results["admin_reset_authorization"] = authorize_disposable_admin_reset(
                    target, environment
                )
        results["outbox_recovery"] = recover_and_drain_disposable_outbox(
            target, environment
        )
        database = target / "data" / "erp.sqlite3"
        with closing(sqlite3.connect(database)) as connection:
            assert connection.execute("SELECT name FROM customers").fetchall() == [("Commit preservado",)]
            assert connection.execute("SELECT COUNT(*) FROM cash_movements").fetchone()[0] == 1
            assert connection.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
            assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
            applied_versions = tuple(
                row[0]
                for row in connection.execute(
                    "SELECT version FROM schema_migrations ORDER BY rowid"
                ).fetchall()
            )
            assert applied_versions == SUPPORTED_SCHEMA_VERSIONS
        doctor = subprocess.run(
            [sys.executable, str(target / "scripts" / "erp_doctor.py")],
            cwd=target,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=45,
        )
        doctor_report = json.loads(doctor.stdout)
        doctor_counts = {
            status: sum(
                1 for finding in doctor_report.get("findings", [])
                if finding.get("status") == status
            )
            for status in ("PASS", "WARN", "FAIL")
        }
        assert doctor.returncode in {0, 1} and doctor_counts["FAIL"] == 0, (
            doctor_counts,
            doctor.stdout[-3000:],
            doctor.stderr[-1500:],
        )
        results.update(
            crash_recovered=True,
            committed_data_preserved=True,
            uncommitted_data_rolled_back=True,
            no_orphan_servers=True,
            integrity="ok",
            remembered_session_after_reopen=all(
                item["remembered_reopen"] for item in results["cycles"][1:3]
            ),
            remember_checkbox_validated=results["cycles"][0]["remember_checkbox_seen"],
            admin_lock_separate=results["cycles"][1]["admin_lock_seen"],
            simple_recovery_ui=results["cycles"][1]["recovery_simple_ui"],
            authorized_reset_ui=results["cycles"][2]["authorized_reset_ui_seen"],
            logout_revoked_remembered_session=(
                results["cycles"][2]["logout_returned_to_login"]
                and results["cycles"][3]["login_after_logout_restart"]
            ),
            doctor={
                "exit_code": doctor.returncode,
                "findings": doctor_counts,
                "warnings": [
                    {
                        "check": finding.get("check"),
                        "reason": finding.get("reason"),
                    }
                    for finding in doctor_report.get("findings", [])
                    if finding.get("status") == "WARN"
                ],
            },
        )
    results["temporary_copy_removed"] = remove_temporary_copy(target)
    assert results["temporary_copy_removed"], "A cópia temporária continuou bloqueada pelo Windows"
    (ARTIFACTS / "desktop_results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(results))


if __name__ == "__main__":
    main()
