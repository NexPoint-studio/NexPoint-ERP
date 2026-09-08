"""Conversões monetárias puras: nenhum banco ou dado operacional é usado."""
from __future__ import annotations

from decimal import Decimal, Inexact, ROUND_DOWN, Rounded, localcontext

import pytest

from app.core.money import MAX_CENTS, MIN_CENTS, cents_to_decimal, decimal_to_cents


@pytest.mark.parametrize("amount,cents", [
    ("0.00", 0), ("0.01", 1), ("10.00", 1000), ("97.35", 9735),
    ("-0.01", -1), ("-97.35", -9735),
    ("9999999999.99", 999999999999),
    ("999999999999.99", 99999999999999),
    ("92233720368547758.07", MAX_CENTS),
    ("-92233720368547758.08", MIN_CENTS),
])
def test_exact_conversion_and_round_trip(amount, cents):
    assert decimal_to_cents(Decimal(amount)) == cents
    result = cents_to_decimal(cents)
    assert result == Decimal(amount)
    assert result.as_tuple().exponent == -2
    assert decimal_to_cents(result) == cents


@pytest.mark.parametrize("amount,cents", [
    ("1.0049", 100), ("1.005", 101), ("1.0051", 101),
    ("-1.0049", -100), ("-1.005", -101), ("-1.0051", -101),
    ("0.005", 1), ("-0.005", -1), ("9.995", 1000),
    ("-0.0001", 0),
    ("92233720368547758.074", MAX_CENTS),
    ("-92233720368547758.084", MIN_CENTS),
])
def test_rounds_half_up_including_signed_ties_and_limits(amount, cents):
    assert decimal_to_cents(Decimal(amount)) == cents


@pytest.mark.parametrize("amount", [
    "92233720368547758.075", "-92233720368547758.085",
    "92233720368547758.08", "-92233720368547758.09",
    "1E+999999999", "-1E+999999999",
])
def test_rejects_amounts_that_round_beyond_int64(amount):
    with pytest.raises(OverflowError):
        decimal_to_cents(Decimal(amount))


@pytest.mark.parametrize("cents", [MIN_CENTS - 1, MAX_CENTS + 1])
def test_rejects_cents_beyond_int64(cents):
    with pytest.raises(OverflowError):
        cents_to_decimal(cents)


@pytest.mark.parametrize("amount", [
    "NaN", "sNaN", "Infinity", "-Infinity",
])
def test_rejects_nonfinite_decimal(amount):
    with pytest.raises(ValueError):
        decimal_to_cents(Decimal(amount))


@pytest.mark.parametrize("amount", [1.25, 1, True, False, "1.25", None])
def test_requires_decimal_without_implicit_conversion(amount):
    with pytest.raises(TypeError):
        decimal_to_cents(amount)


@pytest.mark.parametrize("cents", [1.0, 1.25, True, False, Decimal("1"), "100", None])
def test_requires_integer_cents_without_implicit_conversion(cents):
    with pytest.raises(TypeError):
        cents_to_decimal(cents)


def test_ignores_global_precision_rounding_traps_and_exponent_limits():
    with localcontext() as context:
        context.prec = 6
        context.rounding = ROUND_DOWN
        context.Emin = -1
        context.Emax = 2
        context.traps[Inexact] = True
        context.traps[Rounded] = True
        context.clear_flags()

        assert decimal_to_cents(Decimal("1.005")) == 101
        assert cents_to_decimal(MAX_CENTS) == Decimal("92233720368547758.07")
        assert cents_to_decimal(MIN_CENTS) == Decimal("-92233720368547758.08")
        assert decimal_to_cents(cents_to_decimal(MAX_CENTS)) == MAX_CENTS
        assert decimal_to_cents(cents_to_decimal(MIN_CENTS)) == MIN_CENTS
        assert context.prec == 6
        assert context.rounding == ROUND_DOWN
        assert not any(context.flags.values())


@pytest.mark.parametrize("amount", [
    "1E-999999999", "-1E-999999999", "0E+999999999", "-0E-999999999",
])
def test_extreme_small_exponents_and_signed_zero(amount):
    assert decimal_to_cents(Decimal(amount)) == 0
