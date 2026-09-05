"""Snapshot local pré-testes; nunca inicia o ERP nem altera o banco de origem."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def inventory(path: Path) -> dict:
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as connection:
        names = [row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )]
        tables = {}
        for name in names:
            escaped = name.replace('"', '""')
            rows = connection.execute(f'SELECT * FROM "{escaped}"').fetchall()
            canonical = json.dumps(sorted(rows, key=repr), ensure_ascii=False, default=str).encode()
            tables[name] = {"count": len(rows), "sha256": hashlib.sha256(canonical).hexdigest()}
        return {"tables": tables, "integrity": connection.execute("PRAGMA integrity_check").fetchall(),
                "foreign_keys": connection.execute("PRAGMA foreign_key_check").fetchall()}


def main() -> None:
    database = ROOT / "data" / "erp.sqlite3"
    target = ROOT / "backups" / "pre_testes" / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    target.mkdir(parents=True, exist_ok=False)
    original_hash = hashlib.sha256(database.read_bytes()).hexdigest()
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as source:
        with sqlite3.connect(target / "erp.sqlite3") as destination:
            source.backup(destination)
    current = inventory(database)
    copied = inventory(target / "erp.sqlite3")
    assert current == copied, "O backup não corresponde ao inventário atual."
    report = {"created_at": datetime.now().astimezone().isoformat(), "database": str(database),
              "original_sha256": original_hash, "backup_sha256": hashlib.sha256((target / "erp.sqlite3").read_bytes()).hexdigest(),
              "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
              "git_status": subprocess.check_output(["git", "status", "--short"], cwd=ROOT, text=True), **current}
    (target / "inventory.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"backup": str(target), "original_sha256": original_hash,
                      "counts": {name: data["count"] for name, data in current["tables"].items()},
                      "integrity": current["integrity"], "backup_verified": True}))


if __name__ == "__main__":
    main()
