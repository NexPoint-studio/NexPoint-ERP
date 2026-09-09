"""Cópia portátil, crash controlado e três ciclos reais de pywebview."""
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


def main():
    with tempfile.TemporaryDirectory(prefix="desktop_copy_", dir=ARTIFACTS, ignore_cleanup_errors=True) as temporary:
        target = Path(temporary)
        for name in ("app", "templates", "static"):
            shutil.copytree(ROOT / name, target / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        for name in ("run_desktop.py", "run_dev.py", "pyproject.toml"):
            shutil.copy2(ROOT / name, target / name)
        shutil.copy2(ROOT / "scripts" / "desktop_validation_child.py", target / "validation_child.py")
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
        for cycle in range(1, 4):
            completed = subprocess.run([sys.executable, str(target / "validation_child.py"), str(cycle)], cwd=target, env=environment, capture_output=True, text=True, encoding="utf-8", timeout=55)
            evidence_path = target / f"desktop-{cycle}.json"
            evidence = json.loads(evidence_path.read_text(encoding="utf-8")) if evidence_path.exists() else {}
            results["cycles"].append(evidence)
            assert completed.returncode == 0 and evidence.get("completed"), (cycle, evidence, completed.stderr[-1500:])
            with socket.socket() as probe:
                assert probe.connect_ex(("127.0.0.1", 8877)) != 0, "Servidor órfão na porta 8877"
        database = target / "data" / "erp.sqlite3"
        with closing(sqlite3.connect(database)) as connection:
            assert connection.execute("SELECT name FROM customers").fetchall() == [("Commit preservado",)]
            assert connection.execute("SELECT COUNT(*) FROM cash_movements").fetchone()[0] == 1
            assert connection.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
            assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
            assert connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 6
        results.update(crash_recovered=True, committed_data_preserved=True, uncommitted_data_rolled_back=True,
                       no_orphan_servers=True, integrity="ok", session_after_reopen=all(not item["login_form_seen"] for item in results["cycles"][1:]))
    results["temporary_copy_removed"] = remove_temporary_copy(target)
    assert results["temporary_copy_removed"], "A cópia temporária continuou bloqueada pelo Windows"
    (ARTIFACTS / "desktop_results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(results))


if __name__ == "__main__":
    main()
