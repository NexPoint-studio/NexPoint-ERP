r"""Cria uma variante isolada do demo de dois anos com cenarios financeiros A-D.

Exemplo::

    .\.venv\Scripts\python.exe scripts\seed_financial_demo.py --demo \
        --confirm CRIAR-DEMO-FINANCEIRO \
        --database C:\Temp\demo_2_anos_financeiro.sqlite3

O banco de origem e aberto somente para leitura e o destino deve ser novo e
estar fora do repositorio. O demo oficial de 3000 notas permanece intacto.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timedelta
from decimal import Decimal
import json
import os
from pathlib import Path
import sqlite3
import sys
from uuid import UUID, uuid5

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import select

from app.core.database import build_engine, build_session_factory
from app.models import BillingUnit, CashPaymentMethod, Customer, Service, ServiceNote, ServicePrice, Setting, User
from app.models.receivables import CustomerReceivable
from app.migrations import run_schema_migrations
from app.services.closing import NoteClosingService
from app.services.note_validation import NoteInput, NoteItemInput
from app.services.notes import NoteService
from app.services.payment_validation import PaymentInput
from app.services.payments import PaymentService
from scripts.generate_demo_2_years import DEFAULT_DATABASE, DEMO_PROFILE, OWNER_LOGIN, _validate_target


CONFIRMATION = "CRIAR-DEMO-FINANCEIRO"
SERIES = "DEMO-FINANCEIRO"
SERVICE_CODE = "DEMO-FIN-100"
CUSTOMER_NAME = "Cliente Demo - Cenarios Financeiros"
NAMESPACE = UUID("7b2b03f3-03a8-5482-acd5-0e038b913602")
RECEIVED = datetime(2026, 9, 9, 14, 0)


def _uid(label: str) -> str:
    return str(uuid5(NAMESPACE, label))


def _paths(source: Path, target: Path) -> tuple[Path, Path]:
    checked_source = _validate_target(source)
    checked_target = _validate_target(target)
    if not checked_source.is_file():
        raise RuntimeError("O banco demo de origem nao existe.")
    if checked_target == checked_source:
        raise RuntimeError("O destino deve ser diferente da origem.")
    if checked_target == ROOT / "data" / "erp.sqlite3":
        raise RuntimeError("O banco operacional nao pode ser destino.")
    if checked_target == ROOT or ROOT in checked_target.parents:
        raise RuntimeError("A variante financeira deve ficar fora do projeto oficial.")
    if "demo_2_anos_financeiro" not in checked_target.stem.casefold():
        raise RuntimeError("O destino deve conter demo_2_anos_financeiro no nome.")
    if checked_target.exists():
        raise RuntimeError("O destino ja existe; escolha outro arquivo para preservar dados.")
    return checked_source, checked_target


def _source_count(source: Path) -> int:
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as connection:
        marker = connection.execute(
            "select value from settings where key = 'demo.profile'"
        ).fetchone()
        if marker != (DEMO_PROFILE,):
            raise RuntimeError("A origem nao possui o perfil oficial do demo de dois anos.")
        if connection.execute("pragma integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("O banco demo de origem falhou em integrity_check.")
        if connection.execute("pragma foreign_key_check").fetchone() is not None:
            raise RuntimeError("O banco demo de origem possui violacoes de FK.")
        return int(connection.execute("select count(*) from service_notes").fetchone()[0])


def _note_data(letter: str, customer_id: int, service_id: int, quantity: int) -> NoteInput:
    received = RECEIVED + timedelta(minutes={"A": 0, "B": 10, "C": 20, "D": 30}[letter])
    return NoteInput(
        number=f"DEMO-FIN-{letter}", number_normalized=f"DEMO-FIN-{letter}",
        series=SERIES, series_normalized=SERIES.casefold(),
        customer_id=customer_id, received_at=received,
        expected_ready_at=received + timedelta(days=1),
        notes=f"Cenario {letter} exclusivamente DEMO; dados ficticios.",
        delivery_enabled=False, delivery_amount_text="", delivery_amount=Decimal(0),
        discount_type=None, discount_input_text="", discount_input=Decimal(0),
        items=[NoteItemInput(None, service_id, str(quantity))], revision=None,
    )


def _seed(staging: Path) -> dict[str, int]:
    engine = build_engine("sqlite+pysqlite:///" + staging.as_posix())
    try:
        run_schema_migrations(engine)
        factory = build_session_factory(engine)
        with factory() as session:
            owner = session.scalar(select(User).where(User.email == OWNER_LOGIN))
            unit = session.scalar(select(BillingUnit).where(BillingUnit.code == "UNIT"))
            method = session.scalar(select(CashPaymentMethod).where(CashPaymentMethod.method_kind == "CASH"))
            marker = session.get(Setting, "demo.profile")
            if owner is None or not owner.active or unit is None or method is None or not method.is_active:
                raise RuntimeError("O demo de origem nao possui proprietario, unidade ou forma de pagamento validos.")
            if marker is None or marker.value != DEMO_PROFILE:
                raise RuntimeError("Perfil demo ausente na copia isolada.")
            if session.scalar(select(Service.id).where(Service.code == SERVICE_CODE)) is not None:
                raise RuntimeError("O servico financeiro DEMO ja existe na origem.")
            if session.scalar(select(ServiceNote.id).where(ServiceNote.series_normalized == SERIES.casefold())) is not None:
                raise RuntimeError("A origem ja contem as Notas financeiras DEMO.")
            customer = Customer(
                type="PERSON", name=CUSTOMER_NAME, notes="Cadastro exclusivamente DEMO.",
                is_active=True, created_by=owner.id, updated_by=owner.id,
            )
            service = Service(
                code=SERVICE_CODE, name="Servico unitario para cenarios financeiros DEMO",
                description="Servico ficticio de R$ 100,00 por unidade.",
                category_id=None, billing_unit_id=unit.id, is_active=True,
                created_by=owner.id, updated_by=owner.id,
            )
            session.add_all((customer, service))
            session.flush()
            session.add(ServicePrice(
                service_id=service.id, amount=Decimal("100.00"),
                valid_from=datetime(2026, 9, 8, 0, 0), valid_to=None,
                reason="Preco fixo dos cenarios DEMO", created_by=owner.id,
            ))
            actor_id, customer_id, service_id, method_id = owner.id, customer.id, service.id, method.id
            session.commit()

        note_ids: dict[str, int] = {}
        for letter, quantity in (("A", 3), ("B", 5), ("C", 2)):
            with factory() as session:
                note = NoteService(session, "America/Sao_Paulo").create(
                    _note_data(letter, customer_id, service_id, quantity), actor_id
                )
                note_ids[letter] = note.id
                revision = note.revision
                received = note.received_at
            with factory() as session:
                PaymentService(session).receive(
                    note_ids[letter],
                    PaymentInput(
                        request_uid=_uid(f"payment-{letter}"),
                        payment_method_id=method_id, terminal_id=None,
                        card_mode=None, installments=None,
                        paid_at=received + timedelta(hours=1), revision=revision,
                    ),
                    actor_id,
                    amount_cents={"A": 15_000, "B": 50_000, "C": 10_000}[letter],
                )

        with factory() as session:
            NoteClosingService(session).close(note_ids["C"], _uid("closure-C"), actor_id)
        with factory() as session:
            debt = session.scalar(select(CustomerReceivable).where(
                CustomerReceivable.source_note_id == note_ids["C"]
            ))
            if debt is None or debt.remaining_amount_cents != 10_000:
                raise RuntimeError("O fechamento DEMO nao gerou o saldo de R$ 100,00.")
            debt_id = debt.id
        with factory() as session:
            note = NoteService(session, "America/Sao_Paulo").create(
                _note_data("D", customer_id, service_id, 2), actor_id,
                receivable_ids=[debt_id],
            )
            note_ids["D"] = note.id
        return {"customer_id": customer_id, "service_id": service_id,
                "receivable_id": debt_id, **{f"note_{letter}": note_ids[letter] for letter in "ABCD"}}
    finally:
        engine.dispose()


def validate_financial_demo(path: Path, *, original_notes: int) -> dict[str, object]:
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        rows = {
            str(row["number_original"])[-1]: row
            for row in connection.execute(
                "select id, number_original, customer_id, total_cents, financial_status, "
                "operational_status from service_notes where series_normalized = ?",
                (SERIES.casefold(),),
            )
        }
        if set(rows) != set("ABCD"):
            raise RuntimeError("Os quatro cenarios financeiros DEMO nao foram gerados.")
        count = int(connection.execute("select count(*) from service_notes").fetchone()[0])
        if count != original_notes + 4:
            raise RuntimeError("A variante alterou a contagem das Notas historicas.")
        expected = {
            "A": (30_000, "PARCIAL", "RECEBIDO", 15_000),
            "B": (50_000, "PAGO", "RECEBIDO", 50_000),
            "C": (20_000, "PARCIAL", "FECHADO", 10_000),
            "D": (20_000, "PENDENTE", "RECEBIDO", 0),
        }
        for letter, (total, financial, operational, paid) in expected.items():
            row = rows[letter]
            actual_paid = int(connection.execute(
                "select coalesce(sum(amount_cents),0) from payment_allocations a "
                "join payments p on p.id = a.payment_id "
                "where p.service_note_id = ? and p.status = 'CONFIRMED' "
                "and a.receivable_id is null", (row["id"],),
            ).fetchone()[0])
            if (row["total_cents"], row["financial_status"],
                row["operational_status"], actual_paid) != (total, financial, operational, paid):
                raise RuntimeError(f"O cenario financeiro DEMO {letter} possui valores inconsistentes.")
        closure = connection.execute(
            "select outstanding_amount_cents from note_closures where service_note_id = ?",
            (rows["C"]["id"],),
        ).fetchone()
        debt = connection.execute(
            "select id, original_amount_cents, remaining_amount_cents, status "
            "from customer_receivables where source_note_id = ?",
            (rows["C"]["id"],),
        ).fetchone()
        link = connection.execute(
            "select receivable_id, amount_snapshot_cents from note_receivable_links "
            "where note_id = ?", (rows["D"]["id"],),
        ).fetchone()
        if (closure is None or closure[0] != 10_000 or debt is None
            or tuple(debt)[1:] != (10_000, 10_000, "OPEN")
            or link is None or tuple(link) != (debt["id"], 10_000)):
            raise RuntimeError("O saldo anterior DEMO nao foi fechado e vinculado corretamente.")
        if connection.execute("pragma foreign_key_check").fetchone() is not None:
            raise RuntimeError("A variante financeira DEMO possui violacoes de FK.")
        if connection.execute("pragma integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("A variante financeira DEMO falhou em integrity_check.")
        return {
            "original_notes": original_notes, "variant_notes": count,
            "scenarios": {
                letter: {"note_id": int(rows[letter]["id"]), "total_cents": expected[letter][0],
                         "paid_cents": expected[letter][3]}
                for letter in "ABCD"
            },
            "old_debt_cents": 10_000,
            "linked_to_note_d": True,
        }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Cria uma variante financeira DEMO isolada.")
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--confirm", default="")
    parser.add_argument("--source", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--database", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.demo or args.confirm != CONFIRMATION:
        raise SystemExit(f"Modo demo nao confirmado. Use --demo --confirm {CONFIRMATION}")
    source, target = _paths(args.source, args.database)
    original_notes = _source_count(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    _, target = _paths(source, target)
    staging = target.with_suffix(target.suffix + ".building")
    if staging.exists():
        raise RuntimeError("Existe uma geracao incompleta no destino.")
    try:
        with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as origin:
            with closing(sqlite3.connect(staging)) as copy:
                origin.backup(copy)
        _seed(staging)
        result = validate_financial_demo(staging, original_notes=original_notes)
        if target.exists():
            raise RuntimeError("O destino foi criado durante a geracao; escolha outro nome.")
        os.rename(staging, target)
        print(json.dumps({"database": str(target), **result}, ensure_ascii=False, indent=2))
        return 0
    except BaseException:
        if staging.exists() and staging.is_file() and not staging.is_symlink():
            staging.unlink()
        raise


if __name__ == "__main__":
    raise SystemExit(main())
