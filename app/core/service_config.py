from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BillingUnit:
    code: str
    label: str
    price_suffix: str


BILLING_UNITS = (
    BillingUnit("UNIT", "Por unidade", " / unidade"),
    BillingUnit("KG", "Por kg", " / kg"),
    BillingUnit("PAIR", "Por par", " / par"),
    BillingUnit("METER", "Por metro", " / metro"),
    BillingUnit("FIXED", "Preço fixo", " fixo"),
)
BILLING_UNIT_BY_CODE = {unit.code: unit for unit in BILLING_UNITS}
