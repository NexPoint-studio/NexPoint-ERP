from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Choice:
    code: str
    label: str


MOVEMENT_TYPES = (
    Choice("ENTRY", "Entrada"),
    Choice("EXIT", "Saída"),
)
MOVEMENT_TYPE_BY_CODE = {item.code: item for item in MOVEMENT_TYPES}

CATEGORY_TYPES = (
    Choice("ENTRY", "Entrada"),
    Choice("EXIT", "Saída"),
    Choice("BOTH", "Ambos"),
)
CATEGORY_TYPE_BY_CODE = {item.code: item for item in CATEGORY_TYPES}

MOVEMENT_STATUSES = (
    Choice("ACTIVE", "Ativo"),
    Choice("CANCELED", "Cancelado"),
)
MOVEMENT_STATUS_BY_CODE = {item.code: item for item in MOVEMENT_STATUSES}

PAYMENT_METHOD_DEFAULTS = (
    ("Dinheiro", 10),
    ("Pix", 20),
    ("Cartão", 30),
    ("Boleto", 40),
    ("Outro", 50),
)

PERIOD_SHORTCUTS = (
    Choice("today", "Hoje"),
    Choice("week", "Esta semana"),
    Choice("month", "Este mês"),
    Choice("year", "Este ano"),
    Choice("custom", "Personalizado"),
)
