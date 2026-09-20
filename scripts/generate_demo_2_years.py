r"""Gera o banco persistente e deterministico de dois anos do ambiente demo.

Uso oficial::

    .\.venv\Scripts\python.exe scripts\generate_demo_2_years.py ^
        --demo --confirm CRIAR-DEMO-2-ANOS

O gerador nunca abre nem modifica ``data/erp.sqlite3``. A estrutura nasce das
migrations e do bootstrap oficiais; as linhas volumosas sao inseridas em lote,
reproduzindo as mesmas invariantes de dominio e sendo validadas antes da troca
atomica do arquivo final.
"""
from __future__ import annotations

import argparse
import base64
from collections import Counter, defaultdict
from contextlib import closing
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
import math
import os
from pathlib import Path
import random
import sqlite3
import sys
from uuid import UUID, uuid5
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.sql import insert
from sqlalchemy import select, text

from app.core.config import Settings
from app.core.database import build_engine, build_session_factory
from app.core.money import cents_to_decimal
from app.core.security import SCRYPT_N, SCRYPT_P, SCRYPT_R
from app.models import (
    AuditEvent,
    BillingUnit,
    CashCategory,
    CashMovement,
    CashPaymentMethod,
    Customer,
    CustomerActivity,
    CustomerAddress,
    Payment,
    PaymentFeeRule,
    PaymentTerminal,
    Permission,
    Role,
    Service,
    ServiceCategory,
    ServiceNote,
    ServiceNoteEvent,
    ServiceNoteItem,
    ServicePrice,
    Setting,
    SupportGrant,
    User,
)
from app.services.bootstrap import initialize_database


SEED = 20260909
DEMO_CONFIRMATION = "CRIAR-DEMO-2-ANOS"
DEMO_PROFILE = "nexpoint-2-years-20260909"
DEMO_CONTROL_CENTER_IDENTITY = hashlib.sha256(
    f"nexpoint-demo-control-center:{DEMO_PROFILE}".encode("ascii")
).hexdigest()
OWNER_LOGIN = "proprietario@demo.local"
DEMO_PASSWORD = "Demo#2026!ERP"
COMPANY_NAME = "NexPoint Serviços — Ambiente Demo"
TIMEZONE_NAME = "America/Sao_Paulo"
ZONE = ZoneInfo(TIMEZONE_NAME)
START_LOCAL = datetime(2024, 9, 1, 0, 0)
END_LOCAL = datetime(2026, 9, 9, 23, 59)
DEFAULT_DATABASE = (ROOT / "data" / "demo_2_anos.sqlite3").resolve()
OPERATIONAL_DATABASE = (ROOT / "data" / "erp.sqlite3").resolve()
UUID_NAMESPACE = UUID("6e801f9d-00af-5d66-a543-202609090001")
CUSTOMER_COUNT = 900
NOTE_COUNT = 3000
SERVICE_DEACTIVATED_LOCAL = {
    11: datetime(2026, 6, 15, 18),
    22: datetime(2026, 7, 20, 18),
    33: datetime(2026, 8, 18, 18),
}


CATEGORIES = (
    ("Atendimento e consultoria", "Atendimento especializado e orientacao operacional."),
    ("Limpeza e conservacao", "Servicos de limpeza para ambientes e materiais."),
    ("Instalacoes e manutencao", "Instalacao, revisao e manutencao local."),
    ("Transporte e logistica", "Coletas, entregas e deslocamentos tecnicos."),
    ("Locacao e apoio", "Locacao de equipamentos e apoio por periodo."),
    ("Solucoes por medida", "Servicos medidos por area, extensao ou volume."),
    ("Eventos e pessoas", "Apoio, treinamento e atendimento a grupos."),
    ("Suprimentos e pacotes", "Insumos e pacotes recorrentes de servicos."),
)

# unidade, nome, categoria (1-based), preco atual em centavos
SERVICE_DEFINITIONS = (
    ("UNIT", "Higienizacao de cadeira", 2, 3800),
    ("UNIT", "Revisao de equipamento", 3, 8900),
    ("UNIT", "Montagem de unidade modular", 3, 12500),
    ("FIXED", "Visita tecnica programada", 1, 16500),
    ("FIXED", "Instalacao padrao", 3, 32000),
    ("FIXED", "Diagnostico operacional", 1, 14500),
    ("KG", "Higienizacao textil empresarial", 2, 1680),
    ("KG", "Coleta de reciclaveis selecionados", 4, 920),
    ("KG", "Limpeza de tapetes", 2, 2180),
    ("METER", "Instalacao de rodape", 6, 2850),
    ("METER", "Organizacao de cabeamento", 3, 2150),
    ("METER", "Vedacao linear", 6, 2580),
    ("SQUARE_METER", "Limpeza pos-obra", 2, 1950),
    ("SQUARE_METER", "Impermeabilizacao de superficie", 6, 3650),
    ("SQUARE_METER", "Conservacao de area verde", 2, 1750),
    ("HOUR", "Consultoria de processos", 1, 14800),
    ("HOUR", "Tecnico de manutencao", 3, 11200),
    ("HOUR", "Organizacao documental", 1, 9300),
    ("DAY", "Locacao de lavadora profissional", 5, 21000),
    ("DAY", "Apoio operacional diario", 5, 35500),
    ("DAY", "Locacao de veiculo utilitario", 4, 33000),
    ("SESSION", "Treinamento de operacao", 7, 24500),
    ("SESSION", "Avaliacao de ambiente", 1, 19800),
    ("SESSION", "Organizacao assistida", 1, 22000),
    ("PAIR", "Higienizacao de calcados", 2, 4600),
    ("PAIR", "Ajuste de cortinas", 6, 5900),
    ("PAIR", "Instalacao de par de suportes", 3, 8800),
    ("PERSON", "Apoio para evento", 7, 7600),
    ("PERSON", "Treinamento de equipe", 7, 10800),
    ("PERSON", "Credenciamento presencial", 7, 4900),
    ("KM", "Coleta e entrega local", 4, 390),
    ("KM", "Deslocamento tecnico", 4, 335),
    ("KM", "Transporte leve dedicado", 4, 520),
    ("LITER", "Produto de limpeza concentrado", 8, 2750),
    ("LITER", "Tratamento para pisos", 8, 2150),
    ("LITER", "Reposicao de insumo liquido", 8, 1480),
    ("PACKAGE", "Pacote mensal de manutencao", 8, 89000),
    ("PACKAGE", "Pacote de limpeza compacta", 8, 28500),
    ("PACKAGE", "Pacote de apoio a evento", 8, 54000),
)

CASH_CATEGORIES = (
    ("Aporte eventual", "ENTRY"),
    ("Outras receitas", "ENTRY"),
    ("Aluguel", "EXIT"),
    ("Energia e agua", "EXIT"),
    ("Internet", "EXIT"),
    ("Materiais", "EXIT"),
    ("Manutencao", "EXIT"),
    ("Transporte", "EXIT"),
    ("Fornecedores", "EXIT"),
    ("Servicos administrativos", "EXIT"),
)

FIRST_NAMES = ("Ana", "Bruno", "Carla", "Diego", "Elisa", "Fabio", "Giovana", "Heitor", "Iara", "Jonas", "Lia", "Marcos")
LAST_NAMES = ("Horizonte", "Ipê", "Jardim", "Lago", "Monte", "Nuvem", "Orvalho", "Ponte", "Quintal", "Riacho", "Serra", "Vale")
COMPANY_WORDS = ("Aurora", "Bosque", "Caminho", "Estacao", "Farol", "Girassol", "Horizonte", "Jardim", "Lagoa", "Mirante")


def _uuid(*parts: object) -> str:
    return str(uuid5(UUID_NAMESPACE, ":".join(str(part) for part in parts)))


def _demo_password_hash(email: str) -> str:
    """Mantém o dataset reproduzível com uma credencial pública e apenas demo."""

    salt = hashlib.sha256(f"demo-password:{SEED}:{email}".encode()).digest()[:16]
    digest = hashlib.scrypt(
        DEMO_PASSWORD.encode(),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=32,
    )
    return "$".join(
        (
            "scrypt",
            str(SCRYPT_N),
            str(SCRYPT_R),
            str(SCRYPT_P),
            base64.urlsafe_b64encode(salt).decode(),
            base64.urlsafe_b64encode(digest).decode(),
        )
    )


def _utc(local_value: datetime) -> datetime:
    return local_value.replace(tzinfo=ZONE).astimezone(timezone.utc).replace(tzinfo=None)


def _local(utc_value: datetime) -> datetime:
    return utc_value.replace(tzinfo=timezone.utc).astimezone(ZONE).replace(tzinfo=None)


def _month_key(value: datetime) -> str:
    return value.strftime("%Y-%m")


def _money(cents: int) -> Decimal:
    return cents_to_decimal(int(cents))


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _bulk(session: Session, model, rows: list[dict[str, object]], *, size: int = 500) -> None:
    for offset in range(0, len(rows), size):
        session.execute(insert(model), rows[offset : offset + size])


def _check_digit(base: str, weights: list[int]) -> str:
    remainder = sum(int(digit) * weight for digit, weight in zip(base, weights, strict=True)) % 11
    return str(0 if remainder < 2 else 11 - remainder)


def _cpf(index: int) -> str:
    base = f"9{index:08d}"[-9:]
    first = _check_digit(base, list(range(10, 1, -1)))
    return base + first + _check_digit(base + first, list(range(11, 1, -1)))


def _cnpj(index: int) -> str:
    base = f"98{index:06d}0001"[-12:]
    first = _check_digit(base, [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
    return base + first + _check_digit(base + first, [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])


def _random_between(rng: random.Random, start: datetime, end: datetime) -> datetime:
    minutes = max(0, int((end - start).total_seconds() // 60))
    return start + timedelta(minutes=rng.randint(0, minutes))


def _months() -> list[tuple[int, int]]:
    result: list[tuple[int, int]] = []
    year, month = START_LOCAL.year, START_LOCAL.month
    while (year, month) <= (END_LOCAL.year, END_LOCAL.month):
        result.append((year, month))
        month = month % 12 + 1
        year += int(month == 1)
    return result


def _note_schedule(rng: random.Random) -> list[datetime]:
    season = {1: .76, 2: .80, 3: .95, 4: 1.00, 5: 1.08, 6: .98, 7: .84, 8: .91, 9: 1.12, 10: 1.18, 11: 1.34, 12: 1.46}
    months = _months()
    weights = []
    for index, (_year, month) in enumerate(months):
        partial = .32 if (_year, month) == (2026, 9) else 1.0
        weights.append((.76 + index * .025) * season[month] * partial)
    raw = [NOTE_COUNT * value / sum(weights) for value in weights]
    counts = [math.floor(value) for value in raw]
    for index in sorted(range(len(raw)), key=lambda item: raw[item] - counts[item], reverse=True)[: NOTE_COUNT - sum(counts)]:
        counts[index] += 1

    blackout = {
        date(2024, 12, 25), date(2024, 12, 31), date(2025, 1, 1),
        date(2025, 3, 3), date(2025, 3, 4), date(2025, 12, 25),
        date(2025, 12, 31), date(2026, 1, 1),
    }
    schedule: list[datetime] = []
    weekday_weight = {0: 1.08, 1: 1.02, 2: 1.00, 3: 1.04, 4: 1.18, 5: .40, 6: .11}
    for (year, month), count in zip(months, counts, strict=True):
        next_year, next_month = (year + 1, 1) if month == 12 else (year, month + 1)
        last_day = (date(next_year, next_month, 1) - timedelta(days=1)).day
        if (year, month) == (2026, 9):
            last_day = 9
        days = [date(year, month, day) for day in range(1, last_day + 1) if date(year, month, day) not in blackout]
        day_weights = [weekday_weight[item.weekday()] * (1.12 if item.day in {5, 10, 15, 20, 25} else 1) for item in days]
        for chosen in rng.choices(days, weights=day_weights, k=count):
            hour = rng.choices(range(8, 19), weights=(5, 8, 10, 10, 6, 5, 8, 10, 9, 7, 3), k=1)[0]
            schedule.append(datetime.combine(chosen, time(hour, rng.randrange(0, 60))))
    schedule.sort()
    return schedule


def _validate_target(path: Path) -> Path:
    lexical = Path(os.path.abspath(os.fspath(path.expanduser())))
    existing_parts = [lexical, *lexical.parents]
    for part in existing_parts:
        if not part.exists():
            continue
        attributes = int(getattr(part.stat(follow_symlinks=False), "st_file_attributes", 0))
        if part.is_symlink() or attributes & 0x400:
            raise RuntimeError("O banco demo nao pode usar link simbolico, junction ou reparse point.")
    target = lexical.resolve()
    if target == OPERATIONAL_DATABASE or (
        target.exists()
        and OPERATIONAL_DATABASE.exists()
        and os.path.samefile(target, OPERATIONAL_DATABASE)
    ):
        raise RuntimeError("O banco operacional data/erp.sqlite3 e permanentemente bloqueado.")
    if "demo_2_anos" not in target.stem.casefold():
        raise RuntimeError("O nome do arquivo deve conter demo_2_anos.")
    if ROOT in target.parents and target != DEFAULT_DATABASE:
        raise RuntimeError("Dentro do projeto, somente data/demo_2_anos.sqlite3 e permitido.")
    return target


def _prepare_users_and_settings(factory: sessionmaker[Session]) -> dict[str, int]:
    role_permissions = {
        "demo_atendimento": {
            "customers.view", "customers.create", "customers.edit", "customers.deactivate",
            "customers.activity.create", "services.view", "notes.view", "notes.create",
            "notes.edit", "notes.change_status", "notes.cancel", "payments.receive",
            "cash.operations.view", "cash.create",
        },
        "demo_financeiro": {
            "finance.overview.view", "finance.reports.view", "finance.config.manage",
            "payments.receive", "notes.view", "cash.view", "cash.create", "cash.edit",
            "cash.cancel", "cash.operations.view",
        },
        "demo_operacional": {
            "customers.view", "services.view", "notes.view", "notes.create", "notes.edit",
            "notes.change_status", "notes.cancel",
        },
    }
    users = (
        (OWNER_LOGIN, "Proprietaria Demo", "admin"),
        ("atendimento.ana@demo.local", "Ana Atendimento Demo", "demo_atendimento"),
        ("atendimento.bruno@demo.local", "Bruno Atendimento Demo", "demo_atendimento"),
        ("financeiro@demo.local", "Carla Financeiro Demo", "demo_financeiro"),
        ("operacao@demo.local", "Diego Operacao Demo", "demo_operacional"),
        ("suporte.historico@demo.local", "Suporte Historico Demo", "support"),
    )
    with factory() as session:
        owner = session.scalar(select(User).where(User.email == "admin@local"))
        if owner is None:
            raise RuntimeError("O bootstrap nao criou o Proprietario inicial.")
        owner.email = OWNER_LOGIN
        owner.display_name = "Proprietaria Demo"
        owner.password_hash = _demo_password_hash(OWNER_LOGIN)
        permissions = {row.code: row for row in session.scalars(select(Permission))}
        roles = {row.code: row for row in session.scalars(select(Role))}
        for code, codes in role_permissions.items():
            role = Role(code=code, name={
                "demo_atendimento": "Atendimento Demo",
                "demo_financeiro": "Financeiro Demo",
                "demo_operacional": "Operacao Demo",
            }[code])
            role.permissions = [permissions[item] for item in sorted(codes)]
            session.add(role)
            roles[code] = role
        session.flush()
        for email, display_name, role_code in users[1:]:
            session.add(User(
                email=email,
                display_name=display_name,
                password_hash=_demo_password_hash(email),
                active=True,
                auth_version=1,
                roles=[roles[role_code]],
            ))
        values = {
            "app.name": "ERP NexPoint Demo",
            "company.name": COMPANY_NAME,
            "company.trade_name": "NexPoint Demo",
            "company.document": "",
            "company.phone": "",
            "company.email": "contato@nexpoint-demo.invalid",
            "company.address.street": "Avenida da Demonstracao",
            "company.address.number": "2026",
            "company.address.complement": "Ambiente exclusivamente ficticio",
            "company.address.neighborhood": "Bairro Modelo",
            "company.address.city": "Cidade Demonstracao",
            "company.address.state": "SP",
            "company.address.cep": "00000000",
            "company.timezone": TIMEZONE_NAME,
            "company.currency": "BRL",
            "demo.profile": DEMO_PROFILE,
            "demo.seed": str(SEED),
            "demo.period.start": START_LOCAL.date().isoformat(),
            "demo.period.end": END_LOCAL.date().isoformat(),
            "security.session_generation": _uuid("session-generation"),
        }
        for key, value in values.items():
            row = session.get(Setting, key)
            if row is None:
                session.add(Setting(key=key, value=value))
            else:
                row.value = value
        session.commit()
    with factory() as session:
        return {row.email: row.id for row in session.scalars(select(User))}


def _price_periods(service_id: int, current_cents: int) -> list[tuple[datetime, datetime | None, int]]:
    count = 1 + service_id % 4
    dates_by_count = {
        1: [START_LOCAL],
        2: [START_LOCAL, datetime(2025, 9, 1)],
        3: [START_LOCAL, datetime(2025, 5, 1), datetime(2026, 3, 1)],
        4: [START_LOCAL, datetime(2025, 3, 1), datetime(2025, 11, 1), datetime(2026, 6, 1)],
    }[count]
    factors = {1: [Decimal("1")], 2: [Decimal("0.91"), Decimal("1")], 3: [Decimal("0.84"), Decimal("0.92"), Decimal("1")], 4: [Decimal("0.78"), Decimal("0.86"), Decimal("0.93"), Decimal("1")]}[count]
    result = []
    for index, (start, factor) in enumerate(zip(dates_by_count, factors, strict=True)):
        cents = int((Decimal(current_cents) * factor).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        result.append((_utc(start), _utc(dates_by_count[index + 1]) if index + 1 < len(dates_by_count) else None, cents))
    return result


def _fee_amount(gross_cents: int, percentage_scaled: int, fixed_cents: int) -> int:
    proportional = (Decimal(gross_cents) * Decimal(percentage_scaled) / Decimal(1_000_000)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return int(proportional) + fixed_cents


def _service_quantity(rng: random.Random, unit: dict[str, object]) -> tuple[int, int]:
    behavior = str(unit["quantity_behavior"])
    places = int(unit["decimal_places"])
    code = str(unit["code"])
    if behavior == "FIXED_ONE":
        return 1, 0
    if behavior == "INTEGER":
        maximum = 28 if code == "PERSON" else (6 if code in {"DAY", "SESSION"} else 8)
        return rng.randint(1, maximum), 0
    ranges = {
        "KG": (500, 15_000), "METER": (1_000, 30_000), "SQUARE_METER": (2_000, 65_000),
        "HOUR": (500, 12_000), "KM": (3_000, 120_000), "LITER": (500, 18_000),
    }
    low, high = ranges.get(code, (500, 10_000))
    return rng.randrange(low, high + 1, 250), places


def _subtotal(quantity_scaled: int, places: int, price_cents: int) -> int:
    value = Decimal(quantity_scaled).scaleb(-places) * Decimal(price_cents)
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _status_plan(rng: random.Random, note_id: int, received: datetime) -> dict[str, object]:
    expected = (received + timedelta(days=rng.choices((1, 2, 3, 4, 5, 7, 10), (8, 17, 24, 20, 14, 11, 6), k=1)[0])).replace(hour=17, minute=0)
    age = (END_LOCAL.date() - received.date()).days
    if received.date() >= date(2026, 9, 1) and note_id % 17 == 0:
        status, expected = "EM_ANDAMENTO", datetime(2026, 9, 9, 20, 0)
    elif received.date() < date(2026, 9, 9) and received.date() >= date(2026, 8, 28) and note_id % 19 == 0:
        status, expected = "RECEBIDO", datetime(2026, 9, 8, 17, 0)
    elif age > 45:
        status = rng.choices(("ENTREGUE", "CANCELADO"), (97, 3), k=1)[0]
    elif age > 10:
        status = rng.choices(("ENTREGUE", "PRONTO", "EM_ANDAMENTO", "RECEBIDO", "CANCELADO"), (62, 16, 11, 7, 4), k=1)[0]
    else:
        status = rng.choices(("ENTREGUE", "PRONTO", "EM_ANDAMENTO", "RECEBIDO", "CANCELADO"), (19, 25, 28, 24, 4), k=1)[0]

    transitions: list[tuple[str, datetime, str, str]] = []
    ready = delivery = canceled = None
    if status in {"EM_ANDAMENTO", "PRONTO", "ENTREGUE"}:
        em_at = min(received + timedelta(hours=rng.randint(2, 18)), END_LOCAL)
        transitions.append(("STATUS_CHANGED", em_at, "RECEBIDO", "EM_ANDAMENTO"))
    if status in {"PRONTO", "ENTREGUE"}:
        delay = rng.choices((-1, 0, 1, 2, 3, 5, 8, 12), (12, 51, 13, 9, 6, 4, 3, 2), k=1)[0]
        ready = expected + timedelta(days=delay, hours=rng.randint(-2, 2))
        ready = max(ready, received + timedelta(hours=3))
        if ready > END_LOCAL:
            status, ready = "EM_ANDAMENTO", None
        else:
            transitions.append(("STATUS_CHANGED", ready, "EM_ANDAMENTO", "PRONTO"))
    if status == "ENTREGUE":
        delivery = ready + timedelta(hours=rng.randint(2, 30), days=rng.choices((0, 1, 2, 3), (58, 26, 11, 5), k=1)[0])
        if delivery > END_LOCAL:
            status, delivery = "PRONTO", None
        else:
            transitions.append(("STATUS_CHANGED", delivery, "PRONTO", "ENTREGUE"))
    if status == "CANCELADO":
        if rng.random() < .45:
            em_at = min(received + timedelta(hours=rng.randint(2, 14)), END_LOCAL)
            transitions.append(("STATUS_CHANGED", em_at, "RECEBIDO", "EM_ANDAMENTO"))
            before = "EM_ANDAMENTO"
        else:
            before = "RECEBIDO"
        canceled = min(received + timedelta(hours=rng.randint(4, 72)), END_LOCAL)
        transitions.append(("NOTE_CANCELLED", canceled, before, "CANCELADO"))
    return {"status": status, "expected": expected, "ready": ready, "delivery": delivery, "canceled": canceled, "transitions": transitions}


def _add_audit(rows: list[dict[str, object]], actor: int | None, action: str, resource: str, details: object, at: datetime) -> None:
    rows.append({"user_id": actor, "action": action, "resource": resource[:160], "details": _json(details) if details is not None else None, "created_at": _utc(at)})


def _canonicalize_database(database: Path) -> None:
    """Normaliza rowids e ordem dos índices para reproduzir o mesmo SQLite."""

    with closing(sqlite3.connect(database)) as connection:
        connection.execute("pragma foreign_keys=on")
        indexes = list(
            connection.execute(
                "select name, sql from sqlite_master "
                "where type='index' and sql is not null order by name"
            )
        )
        for name, _sql in indexes:
            quoted = str(name).replace('"', '""')
            connection.execute(f'drop index "{quoted}"')
        for table, columns in (
            ("role_permissions", ("role_id", "permission_id")),
            ("user_roles", ("user_id", "role_id")),
        ):
            names = ", ".join(columns)
            rows = list(connection.execute(f"select {names} from {table} order by {names}"))
            connection.execute(f"delete from {table}")
            placeholders = ", ".join("?" for _column in columns)
            connection.executemany(
                f"insert into {table} ({names}) values ({placeholders})",
                rows,
            )
        connection.commit()
        connection.execute("vacuum")
        for _name, sql in indexes:
            connection.execute(str(sql))
        connection.commit()
        connection.execute("vacuum")


def generate_database(database: Path) -> dict[str, object]:
    rng = random.Random(SEED)
    settings = Settings(
        app_name="ERP NexPoint Demo",
        company_name=COMPANY_NAME,
        database_url=f"sqlite+pysqlite:///{database.as_posix()}",
        session_secret="demo-session-secret-local-20260909-only",
        timezone=TIMEZONE_NAME,
    )
    engine = build_engine(settings.database_url)
    factory = build_session_factory(engine)
    initialize_database(
        engine,
        factory,
        {"admin@local": DEMO_PASSWORD},
        settings,
        control_center_identity=DEMO_CONTROL_CENTER_IDENTITY,
    )
    user_ids = _prepare_users_and_settings(factory)
    owner = user_ids[OWNER_LOGIN]
    attendants = [user_ids["atendimento.ana@demo.local"], user_ids["atendimento.bruno@demo.local"]]
    finance = user_ids["financeiro@demo.local"]
    operation = user_ids["operacao@demo.local"]
    support_user = user_ids["suporte.historico@demo.local"]
    actors = [owner, *attendants, operation]

    audit_rows: list[dict[str, object]] = []
    activity_rows: list[dict[str, object]] = []
    address_rows: list[dict[str, object]] = []
    customer_rows: list[dict[str, object]] = []
    customer_created: dict[int, datetime] = {}
    last_activity: dict[int, datetime] = {}

    catalog_start = _utc(START_LOCAL)
    with factory() as session:
        units = {row.code: {"id": row.id, "code": row.code, "name": row.name, "symbol": row.symbol, "quantity_behavior": row.quantity_behavior, "decimal_places": row.decimal_places} for row in session.scalars(select(BillingUnit))}
        methods = {row.method_kind: {"id": row.id, "name": row.name, "kind": row.method_kind} for row in session.scalars(select(CashPaymentMethod))}
        if set(units) != {item[0] for item in SERVICE_DEFINITIONS}:
            missing = sorted({item[0] for item in SERVICE_DEFINITIONS} - set(units))
            raise RuntimeError(f"Unidades esperadas ausentes: {missing}")
        for row in session.scalars(select(BillingUnit)):
            row.created_at = catalog_start
            row.updated_at = catalog_start
        for row in session.scalars(select(CashPaymentMethod)):
            row.created_at = catalog_start
            row.updated_at = catalog_start
        session.commit()

    category_rows = [{"id": index, "name": name, "description": description, "sort_order": index * 10, "is_active": True, "created_at": catalog_start, "updated_at": catalog_start, "created_by": owner, "updated_by": owner} for index, (name, description) in enumerate(CATEGORIES, 1)]
    service_rows: list[dict[str, object]] = []
    price_rows: list[dict[str, object]] = []
    services: dict[int, dict[str, object]] = {}
    price_id = 1
    for service_id, (unit_code, name, category_id, current_cents) in enumerate(SERVICE_DEFINITIONS, 1):
        deactivated_local = SERVICE_DEACTIVATED_LOCAL.get(service_id)
        service_rows.append({"id": service_id, "code": f"DEM-{service_id:03d}", "name": name, "description": f"Servico ficticio de demonstracao: {name}.", "category_id": category_id, "billing_unit_id": units[unit_code]["id"], "is_active": deactivated_local is None, "created_at": catalog_start, "updated_at": _utc(deactivated_local) if deactivated_local else catalog_start, "created_by": owner, "updated_by": owner})
        periods = _price_periods(service_id, current_cents)
        resolved_periods = []
        for valid_from, valid_to, cents in periods:
            price_rows.append({"id": price_id, "service_id": service_id, "amount": _money(cents), "valid_from": valid_from, "valid_to": valid_to, "reason": "Reajuste historico ficticio" if valid_from != catalog_start else "Preco inicial ficticio", "created_at": valid_from, "created_by": owner})
            resolved_periods.append((valid_from, valid_to, cents, price_id))
            price_id += 1
        services[service_id] = {"id": service_id, "code": f"DEM-{service_id:03d}", "name": name, "description": f"Servico ficticio de demonstracao: {name}.", "category": CATEGORIES[category_id - 1][0], "unit": units[unit_code], "prices": resolved_periods}
        _add_audit(audit_rows, owner, "service.created", f"services/{service_id}", {"demo": True, "unit": unit_code}, START_LOCAL)
        if deactivated_local is not None:
            _add_audit(audit_rows, owner, "service.deactivated", f"services/{service_id}", {"demo": True}, deactivated_local)

    for customer_id in range(1, CUSTOMER_COUNT + 1):
        if customer_id <= 30:
            created_local = START_LOCAL
        elif customer_id <= 360:
            created_local = _random_between(rng, START_LOCAL, datetime(2024, 12, 31, 18))
        else:
            fraction = ((customer_id - 360) / (CUSTOMER_COUNT - 360)) ** 1.12
            created_local = START_LOCAL + (END_LOCAL - START_LOCAL) * fraction
            created_local += timedelta(days=rng.randint(-24, 24), hours=rng.randint(8, 17))
            created_local = min(max(created_local, START_LOCAL), END_LOCAL)
        created = _utc(created_local)
        is_company = customer_id % 4 == 0
        if is_company:
            root = COMPANY_WORDS[customer_id % len(COMPANY_WORDS)]
            name = f"Empresa Demo {root} {customer_id:04d} Ltda."
            trade_name = f"{root} Demo {customer_id:04d}"
        else:
            name = f"Cliente Demo {FIRST_NAMES[customer_id % len(FIRST_NAMES)]} {LAST_NAMES[(customer_id * 7) % len(LAST_NAMES)]} {customer_id:04d}"
            trade_name = None
        actor = actors[customer_id % len(actors)]
        minimum_profile = customer_id % 37 == 0
        document = (_cnpj(customer_id) if is_company else _cpf(customer_id)) if customer_id % 4 in {0, 1} else None
        phone = f"119{customer_id:08d}" if customer_id % 3 else None
        whatsapp = phone if phone and customer_id % 5 else (f"119{customer_id + 10000:08d}" if customer_id % 5 == 0 else None)
        email = f"cliente{customer_id:04d}@demo.invalid" if customer_id % 4 != 2 else None
        birth_date = None if is_company or customer_id % 3 else date(1970 + customer_id % 30, customer_id % 12 + 1, customer_id % 27 + 1)
        primary_contact = f"Contato Demo {customer_id:04d}" if is_company and customer_id % 3 else None
        notes = "Cadastro exclusivamente ficticio do ambiente de demonstracao." if customer_id % 6 == 0 else None
        if minimum_profile:
            trade_name = document = birth_date = primary_contact = None
            phone = whatsapp = email = notes = None
        active = customer_id % 8 != 0
        customer_rows.append({"id": customer_id, "type": "COMPANY" if is_company else "PERSON", "name": name, "trade_name": trade_name, "document": document, "birth_date": birth_date, "primary_contact": primary_contact, "phone": phone, "whatsapp": whatsapp, "email": email, "notes": notes, "is_active": active, "created_at": created, "updated_at": created, "created_by": actor, "updated_by": actor})
        customer_created[customer_id] = created
        last_activity[customer_id] = created
        activity_rows.append({"customer_id": customer_id, "activity_type": "CUSTOMER_CREATED", "occurred_at": created, "description": "Cliente ficticio criado no ambiente demo.", "metadata_json": _json({"demo": True}), "source_type": None, "source_id": None, "source_reference": None, "created_by": actor, "created_at": created})
        if not minimum_profile and (customer_id % 2 == 0 or is_company):
            address_rows.append({"id": len(address_rows) + 1, "customer_id": customer_id, "cep": f"0{customer_id % 10_000_000:07d}", "street": f"Rua Ficticia {customer_id % 47 + 1}", "number": str(100 + customer_id % 900), "complement": f"Unidade Demo {customer_id % 12 + 1}" if customer_id % 5 == 0 else None, "neighborhood": f"Setor Modelo {customer_id % 9 + 1}", "city": "Cidade Demonstracao", "state": ("SP", "MG", "PR", "SC", "RJ")[customer_id % 5]})
        _add_audit(audit_rows, actor, "customer.created", f"customers/{customer_id}", {"demo": True, "type": "COMPANY" if is_company else "PERSON"}, created_local)

    cash_category_rows = [{"id": index, "name": name, "movement_type": movement_type, "description": "Categoria ficticia do ambiente demo.", "sort_order": index * 10, "is_active": True, "created_at": catalog_start, "updated_at": catalog_start, "created_by": finance, "updated_by": finance} for index, (name, movement_type) in enumerate(CASH_CATEGORIES, 1)]
    terminal_rows = [
        {"id": 1, "code": "DEMO_PRINCIPAL", "name": "Terminal Principal Demo", "description": "Terminal ficticio principal.", "sort_order": 10, "is_active": True, "created_at": catalog_start, "updated_at": catalog_start, "created_by": finance, "updated_by": finance},
        {"id": 2, "code": "DEMO_RESERVA", "name": "Terminal Reserva Demo", "description": "Terminal ficticio de contingencia.", "sort_order": 20, "is_active": True, "created_at": catalog_start, "updated_at": catalog_start, "created_by": finance, "updated_by": finance},
    ]
    rate_change = _utc(datetime(2025, 9, 1))
    fee_rule_rows: list[dict[str, object]] = []
    fee_rule_map: dict[tuple[int, int, str, int | None], dict[str, object]] = {}
    fee_id = 1
    for epoch, (valid_from, valid_until) in enumerate(((catalog_start, rate_change), (rate_change, None))):
        for terminal_id in (1, 2):
            base = 1000 * terminal_id + 1800 * epoch
            specs = (("DEBIT", None, 14500 + base, 0), ("CREDIT", None, 42500 + base, 0), ("CREDIT", 1, 29500 + base, 0), ("CREDIT", 2, 36500 + base, 15), ("CREDIT", 3, 40500 + base, 20))
            for mode, installments, percentage, fixed in specs:
                row = {"id": fee_id, "payment_method_id": methods["CARD"]["id"], "terminal_id": terminal_id, "card_mode": mode, "installments": installments, "fee_percentage_scaled": percentage, "fixed_fee_cents": fixed, "valid_from": valid_from, "valid_until": valid_until, "is_active": valid_until is None, "created_at": valid_from, "updated_at": valid_from, "created_by": finance, "updated_by": finance}
                fee_rule_rows.append(row)
                fee_rule_map[(epoch, terminal_id, mode, installments)] = row
                fee_id += 1

    note_rows: list[dict[str, object]] = []
    item_rows: list[dict[str, object]] = []
    event_rows: list[dict[str, object]] = []
    payment_rows: list[dict[str, object]] = []
    system_cash_rows: list[dict[str, object]] = []
    flow_counts: Counter[str] = Counter()
    system_net_by_month: defaultdict[str, int] = defaultdict(int)
    series_counters: defaultdict[str, int] = defaultdict(int)
    payment_id = 0

    def event(note_id: int, event_type: str, at: datetime, actor: int, details: object) -> None:
        event_rows.append({"event_uid": _uuid("event", note_id, len(event_rows) + 1, event_type), "note_id": note_id, "event_type": event_type, "occurred_at": _utc(at), "created_by": actor, "details_json": _json(details)})

    for note_id, received_local in enumerate(_note_schedule(rng), 1):
        received = _utc(received_local)
        eligible = [customer_id for customer_id in range(1, 781) if customer_created[customer_id] <= received]
        customer_id = eligible[min(int((rng.random() ** 2.35) * len(eligible)), len(eligible) - 1)]
        creator = actors[note_id % len(actors)]
        unit_count = rng.choices((1, 2, 3, 4, 5), (47, 31, 14, 6, 2), k=1)[0]
        available_services = [
            service_id
            for service_id in services
            if received_local < SERVICE_DEACTIVATED_LOCAL.get(service_id, END_LOCAL + timedelta(days=1))
        ]
        chosen = {
            note_id
            if note_id <= len(services)
            else rng.choice(available_services)
        }
        while len(chosen) < unit_count:
            chosen.add(rng.choice(available_services))
        subtotal = 0
        for position, service_id in enumerate(sorted(chosen), 1):
            service = services[service_id]
            price = next(cents for start, end, cents, _pid in service["prices"] if start <= received and (end is None or received < end))
            quantity_scaled, places = _service_quantity(rng, service["unit"])
            item_subtotal = _subtotal(quantity_scaled, places, price)
            subtotal += item_subtotal
            item_rows.append({"note_id": note_id, "service_id": service_id, "service_code_snapshot": service["code"], "service_name_snapshot": service["name"], "service_description_snapshot": service["description"], "service_category_name_snapshot": service["category"], "billing_unit_id": service["unit"]["id"], "billing_unit_code_snapshot": service["unit"]["code"], "billing_unit_name_snapshot": service["unit"]["name"], "billing_unit_symbol_snapshot": service["unit"]["symbol"], "quantity_behavior_snapshot": service["unit"]["quantity_behavior"], "decimal_places_snapshot": places, "quantity_scaled": quantity_scaled, "unit_price_cents": price, "subtotal_cents": item_subtotal, "position": position, "created_at": received})

        plan = _status_plan(rng, note_id, received_local)
        zero_total = note_id % 137 == 0
        if zero_total and plan["status"] == "CANCELADO":
            plan = _status_plan(random.Random(SEED + note_id + 1), note_id + 1, received_local)
            if plan["status"] == "CANCELADO":
                plan["status"], plan["transitions"] = "RECEBIDO", []
                plan["canceled"] = None
        delivery_cents = 0 if zero_total or rng.random() > .21 else rng.choice((1200, 1800, 2500, 3500, 4800, 6500))
        discount_type = discount_input = None
        discount_cents = 0
        if zero_total:
            discount_type, discount_cents = "VALOR", subtotal
            discount_input = format(_money(subtotal), "f")
        elif rng.random() < .18:
            discount_type = "PERCENTUAL"
            percentage = rng.choice((Decimal("5"), Decimal("7.5"), Decimal("10"), Decimal("12.5"), Decimal("15")))
            discount_input = format(percentage, "f")
            discount_cents = int((Decimal(subtotal) * percentage / Decimal(100)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        elif rng.random() < .055:
            discount_type = "VALOR"
            discount_cents = min(subtotal, rng.choice((500, 1000, 1500, 2500, 5000)))
            discount_input = format(_money(discount_cents), "f")
        total = subtotal - discount_cents + delivery_cents

        status = str(plan["status"])
        delivery_at = plan["delivery"]
        should_pay = False
        if total > 0 and status != "CANCELADO":
            probability = {"ENTREGUE": .955 if (END_LOCAL - received_local).days > 45 else .82, "PRONTO": .48, "EM_ANDAMENTO": .31, "RECEBIDO": .24}[status]
            should_pay = rng.random() < probability
        payment_flow = None
        paid_local = None
        if should_pay:
            if status == "ENTREGUE":
                payment_flow = rng.choices(("ANTECIPADO", "ENTREGAR_E_RECEBER", "PAGAR_DEPOIS"), (24, 48, 28), k=1)[0]
            else:
                payment_flow = "ANTECIPADO"
            if payment_flow == "ANTECIPADO":
                paid_local = min(received_local + timedelta(hours=rng.randint(1, 48)), END_LOCAL)
                if plan["ready"] is not None:
                    paid_local = min(paid_local, plan["ready"] - timedelta(minutes=10))
                paid_local = max(paid_local, received_local)
            elif payment_flow == "ENTREGAR_E_RECEBER":
                paid_local = delivery_at
            else:
                paid_local = delivery_at + timedelta(days=rng.randint(1, 24), hours=rng.randint(0, 8))
                if paid_local > END_LOCAL:
                    payment_flow, paid_local = "ENTREGAR_E_RECEBER", delivery_at
            flow_counts[payment_flow] += 1

        series = f"{'A' if note_id % 5 else 'B'}-{received_local.year}"
        series_counters[series] += 1
        sequence = series_counters[series]
        number = f"A{sequence}" if note_id % 29 == 0 else (f"{sequence:04d}" if note_id % 3 else str(sequence))
        reference = f"Nota {number} - Serie {series}"
        transitions = list(plan["transitions"])
        combined_delivery = (
            should_pay
            and payment_flow == "ENTREGAR_E_RECEBER"
            and transitions
            and transitions[-1][3] == "ENTREGUE"
        )
        if combined_delivery:
            transitions.pop()
        latest = received_local
        event(note_id, "NOTE_CREATED", received_local, creator, {"operational_status": "RECEBIDO", "financial_status": "PAGO" if zero_total else "PENDENTE", "item_count": unit_count})
        _add_audit(audit_rows, creator, "service_note.created", f"service_notes/{note_id}", {"number": number, "series": series, "total_cents": total}, received_local)
        activity_rows.append({"customer_id": customer_id, "activity_type": "SERVICE_CREATED", "occurred_at": received, "description": f"{reference} criada com {unit_count} item(ns)."[:300], "metadata_json": _json({"service_note_id": note_id, "number_snapshot": number, "series_snapshot": series, "operational_status": "RECEBIDO"}), "source_type": "SERVICE_NOTE", "source_id": str(note_id), "source_reference": reference[:180], "created_by": creator, "created_at": received})
        last_activity[customer_id] = max(last_activity[customer_id], received)

        for event_type, at, before, after in transitions:
            latest = max(latest, at)
            if event_type == "NOTE_CANCELLED":
                event(note_id, event_type, at, creator, {"from": before, "reason": "Cancelamento ficticio solicitado no ambiente demo."})
                _add_audit(audit_rows, creator, "service_note.cancelled", f"service_notes/{note_id}", {"from": before, "reason": "Cancelamento ficticio"}, at)
            else:
                event(note_id, event_type, at, creator, {"from": before, "to": after})
                _add_audit(audit_rows, creator, "service_note.status_changed", f"service_notes/{note_id}", {"from": before, "to": after}, at)
            if after == "PRONTO":
                completed = _utc(at)
                activity_rows.append({"customer_id": customer_id, "activity_type": "SERVICE_COMPLETED", "occurred_at": completed, "description": f"Servicos da {reference} concluidos."[:300], "metadata_json": _json({"service_note_id": note_id, "number_snapshot": number, "series_snapshot": series, "operational_status": "PRONTO"}), "source_type": "SERVICE_NOTE", "source_id": str(note_id), "source_reference": reference[:180], "created_by": creator, "created_at": completed})

        payment_actor = finance if note_id % 3 == 0 else attendants[note_id % 2]
        if should_pay and paid_local is not None:
            payment_id += 1
            method_kind = rng.choices(("CASH", "PIX", "CARD", "BOLETO", "OTHER"), (18, 41, 31, 7, 3), k=1)[0]
            terminal_id = card_mode = installments = terminal_name = fee_rule_id = None
            percentage = fixed = fee = 0
            if method_kind == "CARD":
                terminal_id = 1 if rng.random() < .72 else 2
                terminal_name = terminal_rows[terminal_id - 1]["name"]
                card_mode = rng.choices(("DEBIT", "CREDIT"), (38, 62), k=1)[0]
                installments = 1 if card_mode == "DEBIT" else rng.choices((1, 2, 3, 4, 6), (48, 23, 15, 8, 6), k=1)[0]
                epoch = int(_utc(paid_local) >= rate_change)
                rule_key = (epoch, terminal_id, card_mode, None if card_mode == "DEBIT" else (installments if installments <= 3 else None))
                rule = fee_rule_map[rule_key]
                fee_rule_id = rule["id"]
                percentage = int(rule["fee_percentage_scaled"])
                fixed = int(rule["fixed_fee_cents"])
                fee = _fee_amount(total, percentage, fixed)
            net = total - fee
            request_uid = _uuid("payment", note_id)
            paid = _utc(paid_local)
            payment_rows.append({"id": payment_id, "request_uid": request_uid, "service_note_id": note_id, "customer_id": customer_id, "payment_method_id": methods[method_kind]["id"], "terminal_id": terminal_id, "fee_rule_id": fee_rule_id, "status": "CONFIRMED", "method_name_snapshot": methods[method_kind]["name"], "method_kind_snapshot": method_kind, "terminal_name_snapshot": terminal_name, "card_mode_snapshot": card_mode, "installments": installments, "gross_amount_cents": total, "fee_percentage_scaled": percentage, "fixed_fee_cents": fixed, "fee_amount_cents": fee, "net_amount_cents": net, "paid_at": paid, "created_by": payment_actor, "created_at": paid, "reversed_at": None, "reversed_by": None, "reversal_reason": None})
            system_cash_rows.append({"id": payment_id, "movement_type": "ENTRY", "description": f"Pagamento da {reference}"[:180], "category_id": None, "payment_method_id": methods[method_kind]["id"], "gross_amount": _money(total), "fee_amount": _money(fee), "net_amount": _money(net), "occurred_at": paid, "notes": None, "status": "ACTIVE", "origin": "SYSTEM", "source_reference": reference[:180], "source_type": "PAYMENT", "source_id": str(payment_id), "created_at": paid, "updated_at": paid, "created_by": payment_actor, "updated_by": payment_actor, "canceled_at": None, "canceled_by": None, "cancellation_reason": None})
            event(note_id, "PAYMENT_RECEIVED", paid_local, payment_actor, {"payment_id": payment_id, "cash_movement_id": payment_id, "method": methods[method_kind]["name"], "gross_amount_cents": total, "fee_amount_cents": fee, "net_amount_cents": net})
            if combined_delivery:
                event(note_id, "STATUS_CHANGED", paid_local, payment_actor, {"from": "PRONTO", "to": "ENTREGUE", "payment_id": payment_id})
            revision_at_payment = 1 + sum(
                1 for _event_type, at, _before, _after in transitions if at <= paid_local
            )
            fingerprint = {"service_note_id": note_id, "payment_method_id": methods[method_kind]["id"], "terminal_id": terminal_id, "card_mode": card_mode, "installments": installments, "paid_at": paid.isoformat(timespec="seconds"), "revision": revision_at_payment, "deliver": payment_flow == "ENTREGAR_E_RECEBER"}
            _add_audit(audit_rows, payment_actor, "payment.confirmed", f"payments/{payment_id}", {"request_fingerprint": fingerprint, "cash_movement_id": payment_id, "gross_amount_cents": total, "fee_amount_cents": fee, "net_amount_cents": net, "fee_rule_id": fee_rule_id, "demo_flow": payment_flow}, paid_local)
            _add_audit(audit_rows, payment_actor, "cash_movement.system_created", f"cash_movements/{payment_id}", {"payment_id": payment_id, "service_note_id": note_id}, paid_local)
            latest = max(latest, paid_local)
            system_net_by_month[_month_key(paid_local)] += net

        ready_at = _utc(plan["ready"]) if plan["ready"] is not None else None
        expected = _utc(plan["expected"])
        ready_delay = max(0, (plan["ready"].date() - plan["expected"].date()).days) if plan["ready"] is not None else None
        canceled_at = _utc(plan["canceled"]) if plan["canceled"] is not None else None
        revision = 1 + len(transitions) + int(should_pay)
        note_rows.append({"id": note_id, "revision": revision, "number_original": number, "number_normalized": number, "series_original": series, "series_normalized": series.casefold(), "customer_id": customer_id, "received_at": received, "expected_ready_at": expected, "operational_status": status, "financial_status": "PAGO" if zero_total or should_pay else "PENDENTE", "financial_settlement_reason": "ZERO_TOTAL" if zero_total else ("PAYMENT" if should_pay else None), "delivery_enabled": bool(delivery_cents), "delivery_amount_cents": delivery_cents, "discount_type": discount_type, "discount_input": discount_input, "discount_base_cents": subtotal, "discount_amount_cents": discount_cents, "services_subtotal_cents": subtotal, "total_cents": total, "notes": "Registro historico ficticio do ambiente demo." if note_id % 9 == 0 else None, "ready_at": ready_at, "ready_delay_days": ready_delay, "canceled_at": canceled_at, "canceled_by": creator if canceled_at else None, "cancellation_reason": "Cancelamento ficticio solicitado no ambiente demo." if canceled_at else None, "created_at": received, "updated_at": _utc(latest), "created_by": creator, "updated_by": payment_actor if should_pay else creator})

    # Visitas e alteracoes cadastrais ficticias complementam o historico.
    for sequence in range(1, 451):
        customer_id = 1 + (sequence * 37) % CUSTOMER_COUNT
        at = _random_between(rng, max(_local(customer_created[customer_id]), START_LOCAL), END_LOCAL)
        actor = attendants[sequence % 2]
        activity_rows.append({"customer_id": customer_id, "activity_type": "VISIT", "occurred_at": _utc(at), "description": "Visita manual ficticia registrada para demonstracao.", "metadata_json": None, "source_type": None, "source_id": None, "source_reference": None, "created_by": actor, "created_at": _utc(at)})
        _add_audit(audit_rows, actor, "customer.activity_created", f"customers/{customer_id}", {"activity_type": "VISIT", "demo": True}, at)
        last_activity[customer_id] = max(last_activity[customer_id], _utc(at))
    for customer_id in range(7, CUSTOMER_COUNT + 1, 7):
        at = min(_local(customer_created[customer_id]) + timedelta(days=30 + customer_id % 240), END_LOCAL)
        actor = attendants[customer_id % 2]
        activity_rows.append({"customer_id": customer_id, "activity_type": "CUSTOMER_UPDATED", "occurred_at": _utc(at), "description": "Dados de contato ficticios revisados.", "metadata_json": _json({"fields": ["phone", "email"]}), "source_type": None, "source_id": None, "source_reference": None, "created_by": actor, "created_at": _utc(at)})
        _add_audit(audit_rows, actor, "customer.updated", f"customers/{customer_id}", {"fields": ["phone", "email"], "demo": True}, at)
    for customer_id in range(8, CUSTOMER_COUNT + 1, 8):
        at = min(max(_local(last_activity[customer_id]) + timedelta(days=1), END_LOCAL - timedelta(days=customer_id % 310)), END_LOCAL)
        activity_rows.append({"customer_id": customer_id, "activity_type": "CUSTOMER_DEACTIVATED", "occurred_at": _utc(at), "description": "Cliente ficticio inativado preservando o historico.", "metadata_json": None, "source_type": None, "source_id": None, "source_reference": None, "created_by": owner, "created_at": _utc(at)})
        _add_audit(audit_rows, owner, "customer.deactivated", f"customers/{customer_id}", {"demo": True}, at)
        customer_rows[customer_id - 1]["updated_at"] = _utc(at)
        customer_rows[customer_id - 1]["updated_by"] = owner

    manual_cash_rows: list[dict[str, object]] = []
    movement_id = payment_id
    expense_categories = list(range(3, 11))
    base_weights = [Decimal("0.13"), Decimal("0.04"), Decimal("0.015"), Decimal("0.12"), Decimal("0.07"), Decimal("0.05"), Decimal("0.25"), Decimal("0.325")]
    for month_index, (year, month) in enumerate(_months()):
        key = f"{year:04d}-{month:02d}"
        max_day = 9 if key == "2026-09" else (date(year + int(month == 12), month % 12 + 1, 1) - timedelta(days=1)).day
        manual_entry = 0
        if month_index % 4 == 0:
            manual_entry = rng.randrange(180_000, 650_001, 5_000)
            movement_id += 1
            at_local = datetime(year, month, min(3, max_day), 10, 0)
            manual_cash_rows.append({"id": movement_id, "movement_type": "ENTRY", "description": "Aporte eventual ficticio", "category_id": 1, "payment_method_id": methods["PIX"]["id"], "gross_amount": _money(manual_entry), "fee_amount": _money(0), "net_amount": _money(manual_entry), "occurred_at": _utc(at_local), "notes": "Aporte demonstrativo, sem origem real.", "status": "ACTIVE", "origin": "MANUAL", "source_reference": None, "source_type": None, "source_id": None, "created_at": _utc(at_local), "updated_at": _utc(at_local), "created_by": finance, "updated_by": finance, "canceled_at": None, "canceled_by": None, "cancellation_reason": None})
            _add_audit(audit_rows, finance, "cash_movement.created", f"cash_movements/{movement_id}", {"demo": True, "movement_type": "ENTRY"}, at_local)
        revenue = system_net_by_month[key] + manual_entry
        if month_index in {4, 17}:
            ratio = Decimal("1.12")
        elif month_index in {8, 20}:
            ratio = Decimal("0.99")
        elif month_index in {1, 12}:
            ratio = Decimal("0.93")
        else:
            ratio = Decimal(str(rng.choice((.68, .72, .76, .81, .86))))
        target_expenses = max(350_000, int((Decimal(revenue) * ratio).quantize(Decimal("1"), rounding=ROUND_HALF_UP)))
        jittered = [weight * Decimal(str(rng.uniform(.86, 1.14))) for weight in base_weights]
        allocated = [int(Decimal(target_expenses) * weight / sum(jittered)) for weight in jittered]
        allocated[-1] += target_expenses - sum(allocated)
        for offset, (category_id, cents) in enumerate(zip(expense_categories, allocated, strict=True), 1):
            movement_id += 1
            at_local = datetime(year, month, min(4 + offset * 3, max_day), 9 + offset % 7, 10)
            description = CASH_CATEGORIES[category_id - 1][0]
            method = "BOLETO" if category_id in {3, 5, 9, 10} else "PIX"
            manual_cash_rows.append({"id": movement_id, "movement_type": "EXIT", "description": f"{description} - competencia {key}", "category_id": category_id, "payment_method_id": methods[method]["id"], "gross_amount": _money(cents), "fee_amount": _money(0), "net_amount": _money(cents), "occurred_at": _utc(at_local), "notes": "Despesa operacional ficticia para demonstracao.", "status": "ACTIVE", "origin": "MANUAL", "source_reference": None, "source_type": None, "source_id": None, "created_at": _utc(at_local), "updated_at": _utc(at_local), "created_by": finance, "updated_by": finance, "canceled_at": None, "canceled_by": None, "cancellation_reason": None})
            _add_audit(audit_rows, finance, "cash_movement.created", f"cash_movements/{movement_id}", {"demo": True, "movement_type": "EXIT", "category_id": category_id}, at_local)
        if month_index % 4 == 2:
            movement_id += 1
            at_local = datetime(year, month, min(18, max_day), 14, 0)
            cents = rng.randrange(8_000, 45_001, 500)
            manual_cash_rows.append({"id": movement_id, "movement_type": "EXIT", "description": "Lancamento manual cancelado", "category_id": 6, "payment_method_id": methods["PIX"]["id"], "gross_amount": _money(cents), "fee_amount": _money(0), "net_amount": _money(cents), "occurred_at": _utc(at_local), "notes": "Duplicidade ficticia identificada.", "status": "CANCELED", "origin": "MANUAL", "source_reference": None, "source_type": None, "source_id": None, "created_at": _utc(at_local), "updated_at": _utc(at_local + timedelta(hours=2)), "created_by": finance, "updated_by": finance, "canceled_at": _utc(at_local + timedelta(hours=2)), "canceled_by": finance, "cancellation_reason": "Lancamento ficticio duplicado."})
            _add_audit(audit_rows, finance, "cash_movement.cancelled", f"cash_movements/{movement_id}", {"reason": "Lancamento ficticio duplicado"}, at_local + timedelta(hours=2))

    support_rows = [
        {"id": 1, "grant_uid": _uuid("support", 1), "support_user_id": support_user, "authorized_by": owner, "starts_at": _utc(datetime(2025, 1, 14, 9)), "expires_at": _utc(datetime(2025, 1, 14, 13)), "purpose": "Revisao historica ficticia de indicadores.", "revoked_at": None, "revoked_by": None, "revocation_reason": None, "created_at": _utc(datetime(2025, 1, 14, 8, 55))},
        {"id": 2, "grant_uid": _uuid("support", 2), "support_user_id": support_user, "authorized_by": owner, "starts_at": _utc(datetime(2025, 6, 5, 14)), "expires_at": _utc(datetime(2025, 6, 5, 18)), "purpose": "Apoio ficticio para leitura do sistema.", "revoked_at": _utc(datetime(2025, 6, 5, 15, 20)), "revoked_by": owner, "revocation_reason": "Atendimento demonstrativo concluido.", "created_at": _utc(datetime(2025, 6, 5, 13, 58))},
        {"id": 3, "grant_uid": _uuid("support", 3), "support_user_id": support_user, "authorized_by": owner, "starts_at": _utc(datetime(2025, 11, 18, 8)), "expires_at": _utc(datetime(2025, 11, 18, 12)), "purpose": "Consulta ficticia de auditoria.", "revoked_at": None, "revoked_by": None, "revocation_reason": None, "created_at": _utc(datetime(2025, 11, 18, 7, 55))},
        {"id": 4, "grant_uid": _uuid("support", 4), "support_user_id": support_user, "authorized_by": owner, "starts_at": _utc(datetime(2026, 5, 22, 10)), "expires_at": _utc(datetime(2026, 5, 22, 15)), "purpose": "Validacao ficticia de informacoes locais.", "revoked_at": _utc(datetime(2026, 5, 22, 12, 10)), "revoked_by": owner, "revocation_reason": "Janela demonstrativa encerrada antecipadamente.", "created_at": _utc(datetime(2026, 5, 22, 9, 55))},
    ]
    for row in support_rows:
        local_at = _local(row["created_at"])
        _add_audit(audit_rows, owner, "support.grant_authorized", f"support_grants/{row['grant_uid']}", {"purpose": row["purpose"], "demo": True}, local_at)
        if row["revoked_at"] is not None:
            _add_audit(audit_rows, owner, "support.grant_revoked", f"support_grants/{row['grant_uid']}", {"reason": row["revocation_reason"]}, _local(row["revoked_at"]))

    with factory() as session:
        for table in (Customer, ServiceCategory, Service, ServicePrice, ServiceNote, Payment, CashMovement):
            if session.scalar(select(table).limit(1)) is not None:
                raise RuntimeError(f"A tabela {table.__tablename__} nao esta vazia no banco novo.")
        _bulk(session, ServiceCategory, category_rows)
        _bulk(session, Service, service_rows)
        _bulk(session, ServicePrice, price_rows)
        _bulk(session, CashCategory, cash_category_rows)
        _bulk(session, PaymentTerminal, terminal_rows)
        _bulk(session, PaymentFeeRule, fee_rule_rows)
        _bulk(session, Customer, customer_rows)
        _bulk(session, CustomerAddress, address_rows)
        _bulk(session, ServiceNote, note_rows)
        _bulk(session, ServiceNoteItem, item_rows)
        _bulk(session, Payment, payment_rows)
        _bulk(session, CashMovement, system_cash_rows + manual_cash_rows)
        event_rows.sort(key=lambda row: (row["occurred_at"], row["event_uid"]))
        _bulk(session, ServiceNoteEvent, event_rows)
        _bulk(session, CustomerActivity, activity_rows)
        _bulk(session, SupportGrant, support_rows)
        _bulk(session, AuditEvent, audit_rows)
        migration_time = _utc(START_LOCAL)
        session.execute(
            text("update schema_migrations set applied_at = :applied_at"),
            {"applied_at": migration_time},
        )
        session.commit()
    engine.dispose()
    _canonicalize_database(database)
    return {"flows": dict(sorted(flow_counts.items())), "generated": {"notes": len(note_rows), "items": len(item_rows), "payments": len(payment_rows), "system_cash": len(system_cash_rows), "manual_cash": len(manual_cash_rows), "activities": len(activity_rows), "audits": len(audit_rows)}}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_database(path: Path, generation: dict[str, object]) -> dict[str, object]:
    uri = path.resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        integrity = [row[0] for row in connection.execute("pragma integrity_check")]
        foreign_keys = list(connection.execute("pragma foreign_key_check"))
        if integrity != ["ok"] or foreign_keys:
            raise RuntimeError(f"Falha SQLite: integrity={integrity}, foreign_keys={len(foreign_keys)}")
        marker = connection.execute("select value from settings where key='demo.profile'").fetchone()
        owner = connection.execute("select 1 from users u join user_roles ur on ur.user_id=u.id join roles r on r.id=ur.role_id where u.email=? and u.active=1 and r.code='admin'", (OWNER_LOGIN,)).fetchone()
        if marker is None or marker[0] != DEMO_PROFILE or owner is None:
            raise RuntimeError("O banco nao possui os marcadores seguros do perfil demo.")

        counts = {table: int(connection.execute(f"select count(*) from {table}").fetchone()[0]) for table in (
            "users", "customers", "customer_addresses", "customer_activities", "service_categories",
            "services", "service_prices", "billing_units", "service_notes", "service_note_items",
            "service_note_events", "payments", "cash_movements", "cash_payment_methods", "payment_terminals",
            "payment_fee_rules", "audit_events", "support_grants",
        )}
        counts["minimum_profile_customers"] = int(connection.execute(
            "select count(*) from customers c where "
            "coalesce(c.trade_name,'')='' and coalesce(c.document,'')='' and "
            "coalesce(c.birth_date,'')='' and coalesce(c.primary_contact,'')='' and "
            "coalesce(c.phone,'')='' and coalesce(c.whatsapp,'')='' and "
            "coalesce(c.email,'')='' and coalesce(c.notes,'')='' and "
            "not exists(select 1 from customer_addresses a where a.customer_id=c.id)"
        ).fetchone()[0])
        expected = {"customers": CUSTOMER_COUNT, "minimum_profile_customers": CUSTOMER_COUNT // 37, "services": len(SERVICE_DEFINITIONS), "service_notes": NOTE_COUNT, "billing_units": 13, "payment_terminals": 2, "support_grants": 4}
        for key, value in expected.items():
            if counts[key] != value:
                raise RuntimeError(f"Contagem invalida em {key}: {counts[key]} != {value}")

        invalid_queries = {
            "duplicate_notes": "select count(*) from (select number_normalized, series_normalized from service_notes group by 1,2 having count(*)>1)",
            "note_totals": "select count(*) from service_notes n where n.services_subtotal_cents != (select coalesce(sum(i.subtotal_cents),0) from service_note_items i where i.note_id=n.id) or n.total_cents != n.services_subtotal_cents-n.discount_amount_cents+n.delivery_amount_cents",
            "paid_without_payment": "select count(*) from service_notes n where n.total_cents>0 and n.financial_status='PAGO' and not exists (select 1 from payments p where p.service_note_id=n.id and p.status='CONFIRMED')",
            "pending_with_payment": "select count(*) from service_notes n where n.financial_status='PENDENTE' and exists (select 1 from payments p where p.service_note_id=n.id and p.status='CONFIRMED')",
            "zero_with_finance": "select count(*) from service_notes n where n.total_cents=0 and (n.financial_status!='PAGO' or n.financial_settlement_reason!='ZERO_TOTAL' or exists(select 1 from payments p where p.service_note_id=n.id))",
            "payment_cash_bridge": "select count(*) from payments p left join cash_movements c on c.origin='SYSTEM' and c.source_type='PAYMENT' and c.source_id=cast(p.id as text) where c.id is null or round(c.gross_amount*100)!=p.gross_amount_cents or round(c.fee_amount*100)!=p.fee_amount_cents or round(c.net_amount*100)!=p.net_amount_cents",
            "duplicate_payments": "select count(*) from (select service_note_id from payments where status='CONFIRMED' group by service_note_id having count(*)>1)",
            "payment_customer_mismatch": "select count(*) from payments p join service_notes n on n.id=p.service_note_id where p.customer_id!=n.customer_id",
            "duplicate_system_cash": "select count(*) from (select source_type,source_id from cash_movements where origin='SYSTEM' group by source_type,source_id having count(*)>1)",
            "orphan_system_cash": "select count(*) from cash_movements c where c.origin='SYSTEM' and (c.source_type!='PAYMENT' or not exists(select 1 from payments p where cast(p.id as text)=c.source_id))",
            "canceled_note_finance": "select count(*) from service_notes n where n.operational_status='CANCELADO' and (exists(select 1 from payments p where p.service_note_id=n.id) or exists(select 1 from cash_movements c where c.origin='SYSTEM' and c.source_type='PAYMENT' and c.source_id in (select cast(p.id as text) from payments p where p.service_note_id=n.id)))",
            "zero_with_cash": "select count(*) from service_notes n where n.total_cents=0 and exists(select 1 from payments p join cash_movements c on c.source_type='PAYMENT' and c.source_id=cast(p.id as text) where p.service_note_id=n.id)",
            "price_snapshots": "select count(*) from service_note_items i join service_notes n on n.id=i.note_id where not exists(select 1 from service_prices sp where sp.service_id=i.service_id and sp.valid_from<=n.received_at and (sp.valid_to is null or sp.valid_to>n.received_at) and round(sp.amount*100)=i.unit_price_cents)",
            "fee_rule_snapshots": "select count(*) from payments p left join payment_fee_rules r on r.id=p.fee_rule_id where (p.method_kind_snapshot='CARD' and (r.id is null or r.payment_method_id!=p.payment_method_id or (r.terminal_id is not null and coalesce(r.terminal_id,-1)!=coalesce(p.terminal_id,-1)) or (r.card_mode is not null and coalesce(r.card_mode,'')!=coalesce(p.card_mode_snapshot,'')) or (r.installments is not null and coalesce(r.installments,-1)!=coalesce(p.installments,-1)) or r.fee_percentage_scaled!=p.fee_percentage_scaled or r.fixed_fee_cents!=p.fixed_fee_cents or p.paid_at<r.valid_from or (r.valid_until is not null and p.paid_at>=r.valid_until))) or (p.method_kind_snapshot!='CARD' and (p.fee_rule_id is not null or p.fee_percentage_scaled!=0 or p.fixed_fee_cents!=0 or p.fee_amount_cents!=0))",
            "inactive_service_used_late": "select count(*) from service_note_items i join service_notes n on n.id=i.note_id where (i.service_id=11 and n.received_at>=?) or (i.service_id=22 and n.received_at>=?) or (i.service_id=33 and n.received_at>=?)",
            "created_activities": "select count(*) from service_notes n where (select count(*) from customer_activities a where a.customer_id=n.customer_id and a.activity_type='SERVICE_CREATED' and a.source_type='SERVICE_NOTE' and a.source_id=cast(n.id as text))!=1",
            "completed_activities": "select count(*) from service_notes n where n.ready_at is not null and (select count(*) from customer_activities a where a.customer_id=n.customer_id and a.activity_type='SERVICE_COMPLETED' and a.source_type='SERVICE_NOTE' and a.source_id=cast(n.id as text))!=1",
            "created_events": "select count(*) from service_notes n where (select count(*) from service_note_events e where e.note_id=n.id and e.event_type='NOTE_CREATED')!=1",
            "active_support": "select count(*) from support_grants where revoked_at is null and starts_at<=? and expires_at>?",
        }
        invalid: dict[str, int] = {}
        end_utc = _utc(END_LOCAL).isoformat(sep=" ")
        for name, sql in invalid_queries.items():
            if name == "active_support":
                params = (end_utc, end_utc)
            elif name == "inactive_service_used_late":
                params = tuple(
                    _utc(SERVICE_DEACTIVATED_LOCAL[service_id]).isoformat(sep=" ")
                    for service_id in (11, 22, 33)
                )
            else:
                params = ()
            invalid[name] = int(connection.execute(sql, params).fetchone()[0])
        if any(invalid.values()):
            raise RuntimeError(f"Invariantes invalidas: {invalid}")

        for row in connection.execute("select quantity_scaled, decimal_places_snapshot, unit_price_cents, subtotal_cents from service_note_items"):
            if _subtotal(int(row[0]), int(row[1]), int(row[2])) != int(row[3]):
                raise RuntimeError("Subtotal de item divergente.")
        for row in connection.execute("select gross_amount_cents, fee_percentage_scaled, fixed_fee_cents, fee_amount_cents, net_amount_cents from payments"):
            fee = _fee_amount(int(row[0]), int(row[1]), int(row[2]))
            if fee != int(row[3]) or int(row[4]) != int(row[0]) - fee:
                raise RuntimeError("Snapshot de taxa de pagamento divergente.")

        status_counts = {row[0]: int(row[1]) for row in connection.execute("select operational_status, count(*) from service_notes group by operational_status")}
        method_counts = {row[0]: int(row[1]) for row in connection.execute("select method_kind_snapshot, count(*) from payments group by method_kind_snapshot")}
        cash_counts = {row[0]: int(row[1]) for row in connection.execute("select origin, count(*) from cash_movements group by origin")}
        current = {
            "overdue_open": int(connection.execute("select count(*) from service_notes where operational_status in ('RECEBIDO','EM_ANDAMENTO') and date(expected_ready_at,'-3 hours')<'2026-09-09'").fetchone()[0]),
            "due_today_open": int(connection.execute("select count(*) from service_notes where operational_status in ('RECEBIDO','EM_ANDAMENTO') and date(expected_ready_at,'-3 hours')='2026-09-09'").fetchone()[0]),
            "ready": status_counts.get("PRONTO", 0),
            "delivered_pending": int(connection.execute("select count(*) from service_notes where operational_status='ENTREGUE' and financial_status='PENDENTE'").fetchone()[0]),
        }
        if min(current.values()) <= 0 or set(method_counts) != {"CASH", "PIX", "CARD", "BOLETO", "OTHER"} or set(status_counts) != {"RECEBIDO", "EM_ANDAMENTO", "PRONTO", "ENTREGUE", "CANCELADO"}:
            raise RuntimeError(f"Cobertura operacional insuficiente: current={current}, methods={method_counts}, status={status_counts}")

        gross = int(connection.execute("select coalesce(sum(gross_amount_cents),0) from payments where status='CONFIRMED'").fetchone()[0])
        billed = int(connection.execute("select coalesce(sum(total_cents),0) from service_notes where operational_status!='CANCELADO'").fetchone()[0])
        fees = int(connection.execute("select coalesce(sum(fee_amount_cents),0) from payments where status='CONFIRMED'").fetchone()[0])
        net = int(connection.execute("select coalesce(sum(net_amount_cents),0) from payments where status='CONFIRMED'").fetchone()[0])
        manual_entries = int(connection.execute("select coalesce(round(sum(net_amount)*100),0) from cash_movements where status='ACTIVE' and origin='MANUAL' and movement_type='ENTRY'").fetchone()[0])
        exits = int(connection.execute("select coalesce(round(sum(net_amount)*100),0) from cash_movements where status='ACTIVE' and movement_type='EXIT'").fetchone()[0])
        temporal = []
        for year, month in _months():
            key = f"{year:04d}-{month:02d}"
            row = connection.execute("select count(*), coalesce(sum(total_cents),0) from service_notes where strftime('%Y-%m', datetime(received_at,'-3 hours'))=?", (key,)).fetchone()
            received_row = connection.execute("select coalesce(sum(net_amount_cents),0) from payments where strftime('%Y-%m', datetime(paid_at,'-3 hours'))=?", (key,)).fetchone()
            exit_row = connection.execute("select coalesce(round(sum(net_amount)*100),0) from cash_movements where status='ACTIVE' and movement_type='EXIT' and strftime('%Y-%m', datetime(occurred_at,'-3 hours'))=?", (key,)).fetchone()
            temporal.append({"month": key, "notes": int(row[0]), "notes_total_cents": int(row[1]), "net_received_cents": int(received_row[0]), "exits_cents": int(exit_row[0])})
        yearly: dict[str, dict[str, int | str]] = {}
        for row in temporal:
            year = str(row["month"])[:4]
            bucket = yearly.setdefault(year, {"year": year, "notes": 0, "notes_total_cents": 0, "net_received_cents": 0, "exits_cents": 0})
            for key in ("notes", "notes_total_cents", "net_received_cents", "exits_cents"):
                bucket[key] = int(bucket[key]) + int(row[key])
        relevant = {int(row[0]): row[1] for row in connection.execute("select customer_id,max(occurred_at) from customer_activities where activity_type in ('VISIT','SERVICE_CREATED') group by customer_id")}
        end_utc_dt = _utc(END_LOCAL)
        relationships = {"never": CUSTOMER_COUNT - len(relevant), "30_plus": 0, "60_plus": 0, "90_plus": 0}
        for value in relevant.values():
            parsed = datetime.fromisoformat(value)
            days = (end_utc_dt - parsed).days
            for threshold, key in ((30, "30_plus"), (60, "60_plus"), (90, "90_plus")):
                relationships[key] += int(days >= threshold)

        summary = {
            "profile": DEMO_PROFILE,
            "seed": SEED,
            "period": {"start": START_LOCAL.isoformat(), "end": END_LOCAL.isoformat()},
            "database": str(path.resolve()),
            "sha256": _sha256(path),
            "size_bytes": path.stat().st_size,
            "credentials": {"login": OWNER_LOGIN, "password": DEMO_PASSWORD, "warning": "Credencial exclusivamente demonstrativa"},
            "integrity_check": integrity[0],
            "foreign_key_violations": len(foreign_keys),
            "counts": {**counts, "active_customers": int(connection.execute("select count(*) from customers where is_active=1").fetchone()[0]), "cash_system": cash_counts.get("SYSTEM", 0), "cash_manual": cash_counts.get("MANUAL", 0), "canceled_cash": int(connection.execute("select count(*) from cash_movements where status='CANCELED'").fetchone()[0]), "canceled_notes": status_counts.get("CANCELADO", 0), "zero_total_notes": int(connection.execute("select count(*) from service_notes where total_cents=0").fetchone()[0]), "pending_notes": int(connection.execute("select count(*) from service_notes where financial_status='PENDENTE'").fetchone()[0])},
            "status_counts": status_counts,
            "payment_method_counts": method_counts,
            "payment_flows": generation["flows"],
            "current_operation": current,
            "customer_relationships": relationships,
            "financial_cents": {"gross_billed": billed, "gross_received": gross, "fees": fees, "net_received": net, "manual_entries": manual_entries, "exits": exits, "balance": net + manual_entries - exits, "average_ticket": gross // max(counts["payments"], 1)},
            "average_notes_per_month": round(NOTE_COUNT / len(_months()), 2),
            "temporal": temporal,
            "yearly": list(yearly.values()),
            "invariants": invalid,
        }
        return summary


def _backup_existing(source: Path) -> Path:
    backup = source.with_name(f"{source.stem}.before_regeneration.sqlite3")
    partial = backup.with_suffix(backup.suffix + ".partial")
    if partial.exists():
        partial.unlink()
    source_uri = source.resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(source_uri, uri=True)) as origin:
        with closing(sqlite3.connect(partial)) as target:
            origin.backup(target)
    os.replace(partial, backup)
    return backup


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Gera dois anos de dados exclusivamente ficticios.")
    parser.add_argument("--demo", action="store_true", help="confirma que o destino e um ambiente demo")
    parser.add_argument("--confirm", default="", help=f"deve ser exatamente {DEMO_CONFIRMATION}")
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE, help="destino demo; usado em smoke tests isolados")
    parser.add_argument("--replace-existing", action="store_true", help="substitui um demo existente depois de criar um backup local")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.demo or args.confirm != DEMO_CONFIRMATION:
        raise SystemExit(f"Modo demo nao confirmado. Use --demo --confirm {DEMO_CONFIRMATION}")
    target = _validate_target(args.database)
    target.parent.mkdir(parents=True, exist_ok=True)
    target = _validate_target(args.database)
    if target.exists() and not args.replace_existing:
        raise SystemExit("O banco demo ja existe. Use --replace-existing para regenera-lo com backup.")
    staging = target.with_suffix(target.suffix + ".building")
    if staging.exists():
        raise SystemExit(f"Existe um arquivo de geracao incompleta: {staging}")
    try:
        generation = generate_database(staging)
        summary = validate_database(staging, generation)
        target = _validate_target(args.database)
        previous = _backup_existing(target) if target.exists() else None
        os.replace(staging, target)
        summary["database"] = str(target)
        summary["sha256"] = _sha256(target)
        if previous is not None:
            summary["previous_demo_backup"] = str(previous)
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except BaseException:
        if staging.exists() and staging.is_file() and not staging.is_symlink():
            staging.unlink()
        raise


if __name__ == "__main__":
    raise SystemExit(main())
