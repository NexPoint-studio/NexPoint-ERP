from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import RedirectResponse

from app.core.modules import MODULE_BY_ID
from app.core.note_config import FINANCIAL_STATUS_LABELS, OPERATIONAL_STATUS_LABELS
from app.core.permissions import require_permission
from app.repositories.payments import PaymentRepository
from app.repositories.receivables import ReceivableRepository
from app.observability.context import emit_observability_event
from app.routes.helpers import navigation_context, runtime_timezone, templates
from app.core.money import decimal_to_cents
from app.services.cash_validation import local_now, parse_money
from app.services.payment_validation import PaymentInput
from app.services.payments import (
    PaymentConflictError,
    PaymentConsistencyError,
    PaymentNoteNotFoundError,
    PaymentService,
    PaymentStateError,
    PaymentValidationError,
)


router = APIRouter(prefix="/servicos/notas")
ALLOWED_PAYMENT_FIELDS = frozenset({
    "request_uid", "payment_method_id", "terminal_id", "card_mode",
    "installments", "paid_at", "revision", "deliver", "amount", "notes", "submit",
})


def _context(request: Request, session, **extra):
    module = MODULE_BY_ID["services"]
    tab = next(item for item in module.tabs if item.id == "notas")
    return navigation_context(
        request,
        session,
        selected_module=module,
        selected_tab=tab,
        page_title=extra.pop("page_title", "Registrar pagamento"),
        implemented=True,
        operational_status_labels=OPERATIONAL_STATUS_LABELS,
        financial_status_labels=FINANCIAL_STATUS_LABELS,
        **extra,
    )


def _not_found() -> None:
    raise HTTPException(status_code=404, detail="Nota não encontrada.")


def _form_response(
    request: Request,
    session,
    *,
    note,
    data: PaymentInput,
    deliver: bool,
    errors: dict[str, str],
    status_code: int = 200,
):
    repository = PaymentRepository(session)
    paid_cents = repository.paid_cents_for_note(note.id)
    outstanding_cents = max(0, note.total_cents - paid_cents)
    linked_debts = [
        ReceivableRepository(session).get(link.receivable_id)
        for link in ReceivableRepository(session).links(note.id)
    ]
    linked_outstanding_cents = sum(debt.remaining_amount_cents for debt in linked_debts if debt is not None)
    form_values = data.as_form(runtime_timezone(request, session))
    form_values["amount"] = getattr(data, "amount", "")
    return templates.TemplateResponse(
        request,
        "payments/form.html",
        _context(
            request,
            session,
            note=note,
            form=form_values,
            deliver=deliver,
            errors=errors,
            payment_methods=repository.payment_methods(active_only=True),
            terminals=repository.terminals(active_only=True),
            paid_cents=paid_cents,
            outstanding_cents=outstanding_cents,
            linked_outstanding_cents=linked_outstanding_cents,
            total_collectible_cents=outstanding_cents + linked_outstanding_cents,
            page_title=("Entregar e receber" if deliver else "Registrar pagamento"),
        ),
        status_code=status_code,
    )


def _assert_payable(note, *, deliver: bool, linked_due_cents: int, own_due_cents: int) -> None:
    if own_due_cents + linked_due_cents <= 0:
        raise HTTPException(status_code=409, detail="Esta cobrança já está paga.")
    if note.operational_status == "CANCELADO":
        raise HTTPException(status_code=409, detail="Notas canceladas não podem ser pagas.")
    if note.operational_status == "FECHADO":
        raise HTTPException(
            status_code=409,
            detail="Use a quitação do saldo devedor para uma Nota fechada.",
        )
    if deliver and note.operational_status != "PRONTO":
        raise HTTPException(
            status_code=409,
            detail="Entregar e receber somente está disponível para uma Nota pronta.",
        )


@router.get("/{note_id}/pagamento")
def payment_form(request: Request, note_id: int, deliver: int = 0):
    require_permission(request, "notes.view")
    require_permission(request, "payments.receive")
    if deliver not in {0, 1}:
        raise HTTPException(status_code=422, detail="Ação de entrega inválida.")
    if deliver:
        require_permission(request, "notes.change_status")
    with request.app.state.session_factory() as session:
        timezone_name = runtime_timezone(request, session)
        note = PaymentRepository(session).note(note_id)
        if note is None:
            _not_found()
        own_due_cents = max(0, note.total_cents - PaymentRepository(session).paid_cents_for_note(note.id))
        debts = ReceivableRepository(session)
        linked_due_cents = sum(
            debt.remaining_amount_cents for link in debts.links(note.id)
            if (debt := debts.get(link.receivable_id)) is not None
        )
        _assert_payable(note, deliver=bool(deliver), linked_due_cents=linked_due_cents, own_due_cents=own_due_cents)
        current_utc = local_now(timezone_name).astimezone(timezone.utc).replace(tzinfo=None)
        received_at = note.received_at
        if received_at.tzinfo is not None:
            received_at = received_at.astimezone(timezone.utc).replace(tzinfo=None)
        data = PaymentInput(
            request_uid=str(uuid4()),
            payment_method_id=None,
            terminal_id=None,
            card_mode=None,
            installments=None,
            paid_at=max(current_utc, received_at),
            revision=note.revision,
        )
        return _form_response(
            request, session, note=note, data=data, deliver=bool(deliver), errors={}
        )


@router.post("/{note_id}/pagamento")
async def receive_payment(request: Request, note_id: int):
    require_permission(request, "notes.view")
    actor = require_permission(request, "payments.receive")
    form = await request.form()
    unexpected = {str(key) for key in form.keys()} - ALLOWED_PAYMENT_FIELDS
    if unexpected:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="O formulário contém campos de pagamento não permitidos.",
        )
    raw = {str(key): str(value) for key, value in form.items()}
    # Chamadas anteriores ao suporte a pagamentos parciais não enviavam o
    # campo ``amount``. Nesse caso, o serviço preserva o contrato histórico e
    # recebe todo o saldo cobrável. Se o campo estiver presente, inclusive em
    # branco, ele continua sujeito à validação monetária normal.
    if "amount" not in raw:
        amount_cents = None
        amount_error = ""
    else:
        try:
            amount_cents = decimal_to_cents(
                parse_money(raw.get("amount"), label="valor recebido")
            )
        except ValueError as exc:
            amount_cents = None
            amount_error = str(exc)
        else:
            amount_error = ""
    deliver_raw = raw.get("deliver", "0")
    if deliver_raw not in {"0", "1"}:
        raise HTTPException(status_code=422, detail="Ação de entrega é inválida.")
    deliver = deliver_raw == "1"
    if deliver:
        require_permission(request, "notes.change_status")

    with request.app.state.session_factory() as session:
        timezone_name = runtime_timezone(request, session)
        data = PaymentInput.from_form(raw, timezone_name)
        if amount_error:
            data.errors["amount"] = amount_error
        # A leitura da configuração abre uma transação implícita. O
        # serviço financeiro inicia seu próprio BEGIN IMMEDIATE abaixo.
        session.rollback()
        service = PaymentService(session)
        try:
            service.receive(note_id, data, actor.id, deliver=deliver, amount_cents=amount_cents)
        except PaymentNoteNotFoundError:
            _not_found()
        except PaymentValidationError as exc:
            emit_observability_event(
                module="payment", component="payments_route",
                event_type="payment.rejected", operation="receive",
                status="rejected", user_id=actor.id, severity="WARNING",
                error_code="validation_error", sync_required=True,
            )
            note = PaymentRepository(session).note(note_id)
            if note is None:
                _not_found()
            return _form_response(
                request,
                session,
                note=note,
                data=data,
                deliver=deliver,
                errors=exc.errors,
                status_code=422,
            )
        except (PaymentStateError, PaymentConflictError, PaymentConsistencyError) as exc:
            emit_observability_event(
                module="payment", component="payments_route",
                event_type="payment.rejected", operation="receive",
                status="rejected", user_id=actor.id, severity="WARNING",
                error_code=("payment_conflict" if isinstance(exc, PaymentConflictError)
                            else "payment_state_rejected"),
                sync_required=True,
            )
            note = PaymentRepository(session).note(note_id)
            if note is None:
                _not_found()
            return _form_response(
                request,
                session,
                note=note,
                data=data,
                deliver=deliver,
                errors={"form": str(exc)},
                status_code=409,
            )
    return RedirectResponse(
        f"/servicos/notas/{note_id}?payment_saved=1", status_code=303
    )
