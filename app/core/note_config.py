"""Contrato de domínio compartilhado pelas Notas de Serviço."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from app.services.cash_validation import project_zone


OPERATIONAL_STATUS_LABELS = {
    "RECEBIDO": "Recebido",
    "EM_ANDAMENTO": "Em andamento",
    "PRONTO": "Pronto",
    "ENTREGUE": "Entregue",
    "FECHADO": "Fechado",
    "CANCELADO": "Cancelado",
}
FINANCIAL_STATUS_LABELS = {
    "PENDENTE": "Não pago",
    "PARCIAL": "Parcialmente pago",
    "PAGO": "Pago",
    "SALDO_DEVEDOR": "Saldo devedor",
}
DEADLINE_STATUS_LABELS = {
    "DENTRO_DO_PRAZO": "Dentro do prazo",
    "VENCE_HOJE": "Vence hoje",
    "ATRASADO": "Atrasado",
    "CANCELADO": "Cancelado",
}
DISCOUNT_TYPE_LABELS = {
    "": "Nenhum",
    "VALOR": "Valor",
    "PERCENTUAL": "Percentual",
}

OPERATIONAL_TRANSITIONS = {
    "RECEBIDO": ("EM_ANDAMENTO",),
    "EM_ANDAMENTO": ("PRONTO",),
    "PRONTO": ("ENTREGUE",),
    "ENTREGUE": (),
    "FECHADO": (),
    "CANCELADO": (),
}

QUANTITY_BEHAVIORS = ("INTEGER", "DECIMAL", "FIXED_ONE")
EVENT_TYPES = (
    "NOTE_CREATED", "NOTE_UPDATED", "STATUS_CHANGED", "NOTE_CANCELLED",
    "PAYMENT_RECEIVED", "NOTE_CLOSED", "RECEIVABLE_LINKED",
)
EVENT_TYPE_LABELS = {
    "NOTE_CREATED": "Nota criada",
    "NOTE_UPDATED": "Nota atualizada",
    "STATUS_CHANGED": "Status alterado",
    "NOTE_CANCELLED": "Nota cancelada",
    "PAYMENT_RECEIVED": "Pagamento recebido",
    "NOTE_CLOSED": "Nota fechada",
    "RECEIVABLE_LINKED": "Saldo anterior vinculado",
    "RECEIVABLE_SETTLED": "Saldo devedor recebido",
}

# Limites operacionais da primeira versão. O banco continua sendo a última
# barreira para int64; estes limites mantêm formulários e cálculos previsíveis.
MAX_NOTE_ITEMS = 100
MAX_NOTE_NUMBER_LENGTH = 80
MAX_NOTE_SERIES_LENGTH = 80
MAX_NOTE_SERIES_NORMALIZED_LENGTH = 160
MAX_NOTE_NOTES_LENGTH = 5000
MAX_CANCEL_REASON_LENGTH = 500
MAX_QUANTITY = Decimal("999999999999.999999")
MAX_SCALED_QUANTITY = 999999999999999999
MAX_QUANTITY_DECIMAL_PLACES = 6
PERCENT_DECIMAL_PLACES = 4
MAX_PERCENT = Decimal("100")


@dataclass(frozen=True, slots=True)
class DeadlineInfo:
    code: str
    label: str
    days_late: int
    production_done: bool


def utc_naive(value: datetime) -> datetime:
    """Normaliza um instante para UTC sem tzinfo, padrão usado pelo SQLite local."""
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def utc_to_local(value: datetime, timezone_name: str) -> datetime:
    """Converte datas do SQLite (UTC ingênuo) para o fuso da empresa."""
    try:
        zone = project_zone(timezone_name)
    except RuntimeError:
        raise ValueError("Fuso horário da empresa inválido.") from None
    source = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    return source.astimezone(zone)


def deadline_info(note, timezone_name: str, *, now: datetime | None = None) -> DeadlineInfo:
    """Calcula o prazo por datas locais e congela a referência ao encerrar produção."""
    expected_date = utc_to_local(note.expected_ready_at, timezone_name).date()
    ready_at = getattr(note, "ready_at", None)
    canceled_at = getattr(note, "canceled_at", None)
    production_done = ready_at is not None
    reference = ready_at or canceled_at or now or datetime.now(timezone.utc)
    reference_date = utc_to_local(reference, timezone_name).date()
    days_late = max(0, (reference_date - expected_date).days)

    if note.operational_status == "CANCELADO":
        return DeadlineInfo("CANCELADO", DEADLINE_STATUS_LABELS["CANCELADO"], days_late, production_done)
    if reference_date < expected_date:
        code = "DENTRO_DO_PRAZO"
    elif reference_date == expected_date:
        code = "VENCE_HOJE"
    else:
        code = "ATRASADO"
    return DeadlineInfo(code, DEADLINE_STATUS_LABELS[code], days_late, production_done)
