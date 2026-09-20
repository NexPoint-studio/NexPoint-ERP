"""Conversões exatas para novos valores monetários persistidos em centavos.

Não interpreta formulários nem aplica regras de domínio (como valor positivo).
O Caixa e o catálogo legados continuam usando sua persistência atual.
"""
from __future__ import annotations

from decimal import Context, Decimal, InvalidOperation, ROUND_HALF_UP


MIN_CENTS = -(2**63)
MAX_CENTS = 2**63 - 1
# O Caixa legado persiste NUMERIC(14, 2) no SQLite. Valores maiores que
# doze casas inteiras em reais não têm conversão confiável nesse contrato.
MAX_CASH_CENTS = 99_999_999_999_999
_CENT = Decimal("0.01")


def decimal_to_cents(amount: Decimal) -> int:
    """Arredonda um Decimal para centavos usando ROUND_HALF_UP.

    Aceita negativos; a camada de negócio decide quando eles são válidos.
    Rejeita outros tipos, NaN/infinito e resultados fora do INTEGER do SQLite.
    A precisão, o arredondamento e os traps globais de Decimal não interferem.
    """
    if not isinstance(amount, Decimal):
        raise TypeError("O valor monetário deve ser Decimal.")
    if not amount.is_finite():
        raise ValueError("O valor monetário deve ser finito.")
    if amount.is_zero():
        return 0
    # Nenhum valor com 18 dígitos inteiros cabe em centavos de 64 bits.
    # Rejeitar antes da conversão também evita expandir expoentes enormes.
    if amount.adjusted() > 16:
        raise OverflowError("O valor em centavos excede o INTEGER do SQLite.")

    # 21 dígitos cobrem com folga os 19 dígitos de centavos de int64.
    # O contexto é próprio, inclusive limites e traps, sem herdar o global.
    context = Context(
        prec=21, rounding=ROUND_HALF_UP, Emin=-2, Emax=20,
        traps=[InvalidOperation],
    )
    rounded = amount.quantize(_CENT, context=context)
    cents = int(rounded.scaleb(2, context=context))
    if not MIN_CENTS <= cents <= MAX_CENTS:
        raise OverflowError("O valor em centavos excede o INTEGER do SQLite.")
    return cents


def cents_to_decimal(cents: int) -> Decimal:
    """Converte centavos int64 para Decimal exato com duas casas decimais."""
    if not isinstance(cents, int) or isinstance(cents, bool):
        raise TypeError("Os centavos devem ser um inteiro, sem bool ou float.")
    if not MIN_CENTS <= cents <= MAX_CENTS:
        raise OverflowError("O valor em centavos excede o INTEGER do SQLite.")
    # Construir pela tupla não faz divisão sujeita à precisão global.
    digits = tuple(int(digit) for digit in str(abs(cents)))
    return Decimal((int(cents < 0), digits, -2))
