"""Bateria de volume isolada; os resultados ficam em artifacts, nunca em data."""
from __future__ import annotations

import json
import statistics
import sys
import tempfile
import time
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient
from sqlalchemy import insert, select, text
from app import create_app
from app.models import CashCategory, CashMovement, Customer, Service, ServiceCategory, ServicePrice, User
from app.services.cash import CashReportService
from app.services.cash_validation import resolve_period


def main():
    artifacts = ROOT / "artifacts"
    artifacts.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="stress_", dir=artifacts) as temporary:
        database = Path(temporary) / "stress.sqlite3"
        started = time.perf_counter()
        app = create_app(database_url=f"sqlite+pysqlite:///{database.as_posix()}", credentials={"adm": "adm"})
        startup_ms = (time.perf_counter() - started) * 1000
        now = datetime(2026, 1, 1, 12)
        with app.state.session_factory() as session:
            actor = session.scalar(select(User.id))
            common = {"created_by": actor, "updated_by": actor, "is_active": True}
            session.execute(insert(Customer), [{**common, "name": f"Cliente fictício {i:05d}", "type": "PERSON", "phone": f"119{i:08d}"} for i in range(5000)])
            session.execute(insert(ServiceCategory), [{**common, "id": i + 1, "name": f"Categoria serviço {i:02d}", "sort_order": i} for i in range(25)])
            session.execute(insert(CashCategory), [{**common, "id": i + 1, "name": f"Categoria caixa {i:02d}", "movement_type": "BOTH", "sort_order": i} for i in range(25)])
            session.execute(insert(Service), [{**common, "id": i + 1, "name": f"Serviço fictício {i:04d}", "code": f"ST{i:04d}", "category_id": i % 25 + 1, "billing_unit": ["UNIT", "KG", "PAIR", "METER", "FIXED"][i % 5]} for i in range(1000)])
            session.execute(insert(ServicePrice), [{"service_id": i + 1, "amount": Decimal(i + 1) / 100, "created_by": actor} for i in range(1000)])
            rows = []
            expected = {"entries": 0, "exits": 0, "fees": 0}
            for i in range(20000):
                kind = "ENTRY" if i % 3 else "EXIT"
                gross = i % 100000 + 1
                fee = min(15, gross) if kind == "ENTRY" and i % 5 == 0 else 0
                status = "CANCELED" if i % 37 == 0 else "ACTIVE"
                if status == "ACTIVE":
                    expected["entries" if kind == "ENTRY" else "exits"] += gross - fee
                    expected["fees"] += fee
                rows.append({"movement_type": kind, "description": f"Movimento fictício {i:05d}",
                    "category_id": i % 25 + 1, "payment_method_id": i % 5 + 1,
                    "gross_amount": Decimal(gross) / 100, "fee_amount": Decimal(fee) / 100,
                    "net_amount": Decimal(gross - fee) / 100, "occurred_at": now + timedelta(days=i % 365),
                    "status": status, "origin": "MANUAL", "created_by": actor, "updated_by": actor})
            session.execute(insert(CashMovement), rows)
            session.commit()
            report = CashReportService(session, "America/Sao_Paulo").period_report(resolve_period("custom", "2026-01-01", "2026-12-31", "America/Sao_Paulo"))
            assert report.totals.entries == Decimal(expected["entries"]) / 100
            assert report.totals.exits == Decimal(expected["exits"]) / 100
            assert report.totals.fees == Decimal(expected["fees"]) / 100
            assert report.final_balance == Decimal(expected["entries"] - expected["exits"]) / 100
            assert session.execute(text("PRAGMA foreign_key_check")).all() == []
            indexes = {table: [list(row) for row in session.execute(text(f"PRAGMA index_list('{table}')"))]
                       for table in ("customers", "services", "cash_movements")}
            plans = {"cash_period": [list(row) for row in session.execute(text("EXPLAIN QUERY PLAN SELECT id FROM cash_movements WHERE occurred_at >= '2026-09-01' AND occurred_at < '2026-10-01' ORDER BY occurred_at DESC LIMIT 25"))],
                     "customer_document": [list(row) for row in session.execute(text("EXPLAIN QUERY PLAN SELECT id FROM customers WHERE document='00000000000'"))]}
        measurements = {}
        with TestClient(app) as client:
            assert client.post("/login", data={"email": "adm", "password": "adm"}, follow_redirects=False).status_code == 303
            routes = {"customers_list": "/clientes/lista", "customers_search": "/clientes/lista?q=0123",
                      "customers_filter": "/clientes/lista?type=PERSON&active=ACTIVE&relationship=NEVER",
                      "customers_page": "/clientes/lista?page=150", "services_list": "/servicos/catalogo",
                      "cash_history": "/caixa/historico?period=year", "cash_month_report": "/caixa/relatorios?period=custom&start=2026-09-01&end=2026-09-30",
                      "cash_year_report": "/caixa/relatorios?period=custom&start=2026-01-01&end=2026-12-31"}
            for name, path in routes.items():
                elapsed = []
                for _ in range(3):
                    started = time.perf_counter()
                    response = client.get(path)
                    elapsed.append(round((time.perf_counter() - started) * 1000, 2))
                    assert response.status_code == 200, (path, response.status_code)
                measurements[name] = {"median_ms": statistics.median(elapsed), "samples_ms": elapsed}
        evidence = {"counts": {"customers": 5000, "services": 1000, "categories": 50, "service_prices": 1000, "cash_movements": 20000},
                    "startup_ms": round(startup_ms, 2), "measurements": measurements, "expected_cents": expected,
                    "independent_financial_match": True, "indexes": indexes, "explain": plans,
                    "database": "Temporário; removido após validação"}
        (artifacts / "stress_results.json").write_text(json.dumps(evidence, indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps({key: value for key, value in evidence.items() if key not in {"indexes", "explain"}}))


if __name__ == "__main__":
    main()
