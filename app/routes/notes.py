from __future__ import annotations

from urllib.parse import urlencode
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import select

from app.core.modules import MODULE_BY_ID
from app.core.note_config import (
    DEADLINE_STATUS_LABELS,
    EVENT_TYPE_LABELS,
    FINANCIAL_STATUS_LABELS,
    OPERATIONAL_STATUS_LABELS,
)
from app.core.permissions import require_permission
from app.routes.helpers import navigation_context, runtime_timezone, templates
from app.repositories.payments import PaymentRepository
from app.services.cash_validation import local_now, parse_money
from app.core.money import decimal_to_cents
from app.services.note_validation import NoteInput
from app.services.payment_validation import PaymentInput
from app.services.payments import (
    PaymentConflictError,
    PaymentConsistencyError,
    PaymentNoteNotFoundError,
    PaymentService,
    PaymentStateError,
    PaymentValidationError,
)
from app.services.notes import (
    DuplicateNoteNumberError,
    NoteConflictError,
    NoteNotFoundError,
    NoteService,
    NoteStateError,
    NoteValidationError,
)
from app.repositories.receivables import ReceivableRepository
from app.models.auth import User
from app.observability.context import emit_observability_event
from app.services.closing import NoteClosingConflict, NoteClosingError, NoteClosingService


router = APIRouter(prefix="/servicos")


DEADLINE_OPTIONS = {
    "OVERDUE": DEADLINE_STATUS_LABELS["ATRASADO"],
    "TODAY": DEADLINE_STATUS_LABELS["VENCE_HOJE"],
}
def _context(request: Request, session, tab_id: str, **extra):
    module = MODULE_BY_ID["services"]
    tab = next(item for item in module.tabs if item.id == tab_id)
    return navigation_context(
        request,
        session,
        selected_module=module,
        selected_tab=tab,
        page_title=extra.pop("page_title", tab.name),
        implemented=True,
        operational_status_labels=OPERATIONAL_STATUS_LABELS,
        financial_status_labels=FINANCIAL_STATUS_LABELS,
        event_type_labels=EVENT_TYPE_LABELS,
        **extra,
    )


def _not_found() -> None:
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Nota não encontrada")


def _new_form(timezone_name: str) -> dict[str, object]:
    received = local_now(timezone_name).replace(second=0, microsecond=0)
    return {
        "number": "",
        "series": "",
        "customer_id": "",
        "received_at": received.strftime("%Y-%m-%dT%H:%M"),
        "expected_ready_at": "",
        "notes": "",
        "delivery_enabled": "0",
        "delivery_amount": "",
        "discount_type": "",
        "discount_input": "",
        "items": [],
        "initial_payment_enabled": "0",
        "initial_payment_request_uid": str(uuid4()),
        "initial_payment_amount": "",
        "initial_payment_method_id": "",
        "initial_payment_terminal_id": "",
        "initial_payment_card_mode": "",
        "initial_payment_installments": "1",
        "initial_payment_paid_at": received.strftime("%Y-%m-%dT%H:%M"),
        "initial_payment_notes": "",
    }


def _preserve_initial_payment(form: dict[str, object], raw) -> dict[str, object]:
    preserved = dict(form)
    for name in (
        "initial_payment_enabled", "initial_payment_request_uid",
        "initial_payment_amount", "initial_payment_method_id",
        "initial_payment_terminal_id", "initial_payment_card_mode",
        "initial_payment_installments", "initial_payment_paid_at",
        "initial_payment_notes",
    ):
        if name in raw:
            preserved[name] = str(raw.get(name) or "")
    return preserved


def _initial_payment_input(raw, timezone_name: str) -> tuple[PaymentInput, int | None]:
    mapped = {
        "request_uid": raw.get("initial_payment_request_uid"),
        "payment_method_id": raw.get("initial_payment_method_id"),
        "terminal_id": raw.get("initial_payment_terminal_id"),
        "card_mode": raw.get("initial_payment_card_mode"),
        "installments": raw.get("initial_payment_installments"),
        "paid_at": raw.get("initial_payment_paid_at"),
        "notes": raw.get("initial_payment_notes"),
        # A revisão autoritativa é substituída depois que a Nota é inserida.
        "revision": "1",
    }
    data = PaymentInput.from_form(mapped, timezone_name)
    try:
        amount_cents = decimal_to_cents(
            parse_money(raw.get("initial_payment_amount"), label="valor recebido")
        )
    except ValueError as exc:
        amount_cents = None
        data.errors["amount"] = str(exc)
    return data, amount_cents


def _form_response(
    request: Request,
    session,
    service: NoteService,
    *,
    form: dict[str, object],
    errors: dict[str, str],
    item_errors: list[dict[str, str]],
    editing: bool,
    note=None,
    selected_receivable_ids: list[int] | None = None,
    status_code: int = 200,
):
    raw_customer_id = form.get("customer_id") if hasattr(form, "get") else None
    try:
        selected_customer_id = int(str(raw_customer_id or ""))
    except ValueError:
        selected_customer_id = None
    if note is not None:
        selected_customer_id = note.customer_id
    customers = service.form_customers(
        selected_id=selected_customer_id,
        preserve_inactive_selected=note is not None,
    )
    open_receivables = (
        ReceivableRepository(session).open_for_customer(selected_customer_id)
        if selected_customer_id else []
    )
    return templates.TemplateResponse(
        request,
        "notes/form.html",
        _context(
            request,
            session,
            "notas" if editing else "nova-nota",
            form=form,
            errors=errors,
            item_errors=item_errors,
            customers=customers,
            available_services=service.available_services(),
            editing=editing,
            note=note,
            open_receivables=open_receivables,
            selected_receivable_ids=selected_receivable_ids or [],
            payment_methods=PaymentRepository(session).payment_methods(active_only=True),
            payment_terminals=PaymentRepository(session).terminals(active_only=True),
            page_title=(f"Editar Nota {note.number_original}" if editing else "Nova Nota de Serviço"),
        ),
        status_code=status_code,
    )


def _detail_response(request: Request, session, profile, *, status_code: int = 200, **extra):
    payments = PaymentRepository(session)
    payment_rows = payments.confirmed_payments_for_note(profile.note.id)
    payment_cash = {row.id: payments.cash_for_payment(row.id) for row in payment_rows}
    note_payment_cents = payments.paid_cents_for_note(profile.note.id)
    receivables = ReceivableRepository(session)
    linked_debts = [(link, receivables.get(link.receivable_id)) for link in receivables.links(profile.note.id)]
    linked_remaining_cents = sum(debt.remaining_amount_cents for _, debt in linked_debts if debt is not None)
    payment_allocations = {row.id: receivables.allocations_for_payment(row.id) for row in payment_rows}
    closure = receivables.closure(profile.note.id)
    receivable = receivables.for_note(profile.note.id)
    receivable_payments = receivables.payments_for(receivable.id) if receivable is not None else []
    debt_paid_cents = (
        receivable.original_amount_cents - receivable.remaining_amount_cents
        if receivable is not None else 0
    )
    paid_cents = min(profile.note.total_cents, note_payment_cents + debt_paid_cents)
    outstanding_cents = max(0, profile.note.total_cents - paid_cents)
    actor_ids = {row.created_by for row in (*payment_rows, *receivable_payments)}
    payment_actor_names = {
        actor.id: actor.display_name
        for actor in session.scalars(select(User).where(User.id.in_(actor_ids)))
    } if actor_ids else {}
    if closure is not None and outstanding_cents > 0:
        financial_status_derived = "SALDO_DEVEDOR"
    elif paid_cents >= profile.note.total_cents:
        financial_status_derived = "PAGO"
    elif paid_cents:
        financial_status_derived = "PARCIAL"
    else:
        financial_status_derived = "PENDENTE"
    return templates.TemplateResponse(
        request,
        "notes/detail.html",
        _context(
            request,
            session,
            "notas",
            profile=profile,
            payment=payment_rows[-1] if payment_rows else None,
            payments=payment_rows,
            payment_cash=payment_cash,
            payment_allocations=payment_allocations,
            receivable_payments=receivable_payments,
            payment_actor_names=payment_actor_names,
            paid_cents=paid_cents,
            outstanding_cents=outstanding_cents,
            linked_debts=linked_debts,
            linked_remaining_cents=linked_remaining_cents,
            total_collectible_cents=outstanding_cents + linked_remaining_cents,
            financial_status_derived=financial_status_derived,
            closure=closure,
            receivable=receivable,
            payment_methods=payments.payment_methods(active_only=True),
            receivable_payment_uid=str(uuid4()),
            receivable_paid_at=local_now(runtime_timezone(request, session)).strftime("%Y-%m-%dT%H:%M"),
            close_request_uid=str(uuid4()),
            page_title=f"Nota {profile.note.number_original}",
            **extra,
        ),
        status_code=status_code,
    )


@router.post("/notas/{note_id}/fechar")
async def close_note(request: Request, note_id: int):
    user = require_permission(request, "notes.change_status")
    raw = await request.form()
    request_uid = str(raw.get("request_uid") or "")
    with request.app.state.session_factory() as session:
        session.rollback()
        try:
            NoteClosingService(session).close(note_id, request_uid, user.id)
        except (NoteClosingError, NoteClosingConflict) as exc:
            emit_observability_event(
                module="note", component="notes_route",
                event_type="note.close.failed", operation="close",
                status="failed", user_id=user.id, severity="WARNING",
                error_code=("close_conflict" if isinstance(exc, NoteClosingConflict)
                            else "close_rejected"),
                sync_required=True,
            )
            service = NoteService(session, runtime_timezone(request, session))
            try:
                profile = service.profile(note_id)
            except NoteNotFoundError:
                _not_found()
            return _detail_response(request, session, profile, action_error=str(exc), status_code=409)
    return RedirectResponse(f"/servicos/notas/{note_id}?status_changed=1", status_code=303)


@router.get("/nova-nota")
def new_note(request: Request):
    require_permission(request, "notes.create")
    with request.app.state.session_factory() as session:
        timezone_name = runtime_timezone(request, session)
        service = NoteService(session, timezone_name)
        return _form_response(
            request,
            session,
            service,
            form=_new_form(timezone_name),
            errors={},
            item_errors=[],
            editing=False,
        )


@router.post("/nova-nota")
async def create_note(request: Request):
    user = require_permission(request, "notes.create")
    raw = await request.form()
    with request.app.state.session_factory() as session:
        timezone_name = runtime_timezone(request, session)
        data = NoteInput.from_form(raw, timezone_name)
        service = NoteService(session, timezone_name)
        try:
            selected_debts = [int(str(value)) for value in raw.getlist("receivable_ids")]
        except ValueError:
            selected_debts = [0]
        initial_payment_raw = str(raw.get("initial_payment_enabled") or "0")
        if initial_payment_raw not in {"0", "1"}:
            data.errors["initial_payment_form"] = "A opção de pagamento inicial é inválida."
        initial_payment_enabled = initial_payment_raw == "1"
        payment_data = None
        amount_cents = None
        if initial_payment_enabled:
            require_permission(request, "payments.receive")
            payment_data, amount_cents = _initial_payment_input(raw, timezone_name)
        try:
            note = service.create(
                data,
                user.id,
                receivable_ids=selected_debts,
                commit=not initial_payment_enabled,
            )
            if payment_data is not None:
                payment_data.revision = note.revision
                PaymentService(session).receive(
                    note.id,
                    payment_data,
                    user.id,
                    amount_cents=amount_cents,
                    begin_immediate=False,
                )
        except NoteValidationError as exc:
            data = exc.data
        except DuplicateNoteNumberError:
            data.errors["number_series"] = "Já existe uma Nota com este número e esta série/bloco."
            return _form_response(
                request,
                session,
                service,
                form=_preserve_initial_payment(data.as_form(timezone_name), raw),
                errors=data.errors,
                item_errors=data.item_errors,
                editing=False,
                selected_receivable_ids=selected_debts,
                status_code=status.HTTP_409_CONFLICT,
            )
        except PaymentValidationError as exc:
            for key, message in exc.errors.items():
                data.errors[f"initial_payment_{key}" if key != "form" else "initial_payment_form"] = message
            return _form_response(
                request,
                session,
                service,
                form=_preserve_initial_payment(data.as_form(timezone_name), raw),
                errors=data.errors,
                item_errors=data.item_errors,
                editing=False,
                selected_receivable_ids=selected_debts,
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            )
        except (PaymentStateError, PaymentConflictError, PaymentConsistencyError) as exc:
            data.errors["initial_payment_form"] = str(exc)
            return _form_response(
                request,
                session,
                service,
                form=_preserve_initial_payment(data.as_form(timezone_name), raw),
                errors=data.errors,
                item_errors=data.item_errors,
                editing=False,
                selected_receivable_ids=selected_debts,
                status_code=status.HTTP_409_CONFLICT,
            )
        except PaymentNoteNotFoundError:
            data.errors["initial_payment_form"] = "A Nota não pôde ser vinculada ao pagamento."
            return _form_response(
                request,
                session,
                service,
                form=_preserve_initial_payment(data.as_form(timezone_name), raw),
                errors=data.errors,
                item_errors=data.item_errors,
                editing=False,
                selected_receivable_ids=selected_debts,
                status_code=status.HTTP_409_CONFLICT,
            )
        else:
            return RedirectResponse(f"/servicos/notas/{note.id}?saved=1", status_code=303)
        return _form_response(
            request,
            session,
            service,
            form=_preserve_initial_payment(data.as_form(timezone_name), raw),
            errors=data.errors,
            item_errors=data.item_errors,
            editing=False,
            selected_receivable_ids=selected_debts,
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )


@router.get("/notas/debitos")
def note_customer_debts(request: Request, customer_id: int):
    require_permission(request, "notes.create")
    if customer_id <= 0 or customer_id >= 2**63:
        raise HTTPException(status_code=422, detail="Cliente inválido.")
    with request.app.state.session_factory() as session:
        debts = ReceivableRepository(session).open_for_customer(customer_id)
        rows = [{
            "id": debt.id,
            "source_note_id": debt.source_note_id,
            "amount_display": f"R$ {debt.remaining_amount_cents // 100:,}".replace(",", ".")
                + f",{debt.remaining_amount_cents % 100:02d}",
        } for debt in debts]
    response = JSONResponse({"items": rows})
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/notas")
def list_notes(
    request: Request,
    q: str = "",
    customer_id: str = "",
    operational_status: str = "ALL",
    financial_status: str = "ALL",
    deadline: str = "ALL",
    date_from: str = "",
    date_to: str = "",
    page: int = 1,
    per_page: int = 25,
):
    require_permission(request, "notes.view")
    parsed_customer = None
    if customer_id:
        if not customer_id.isascii() or not customer_id.isdecimal() or len(customer_id) > 19:
            raise HTTPException(status_code=422, detail="Selecione um cliente válido.")
        parsed_customer = int(customer_id)
        if not 1 <= parsed_customer < 2**63:
            raise HTTPException(status_code=422, detail="Selecione um cliente válido.")
    filters = {
        "q": q[:120],
        "customer_id": customer_id,
        "operational_status": operational_status,
        "financial_status": financial_status,
        "deadline": deadline,
        "date_from": date_from,
        "date_to": date_to,
        "per_page": str(per_page),
    }
    with request.app.state.session_factory() as session:
        timezone_name = runtime_timezone(request, session)
        service = NoteService(session, timezone_name)
        try:
            result = service.list(
                search=q,
                customer_id=parsed_customer,
                operational_status=operational_status,
                financial_status=financial_status,
                deadline_status=deadline,
                date_from=date_from,
                date_to=date_to,
                page=page,
                per_page=per_page,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None

        def page_url(target: int) -> str:
            query = {key: value for key, value in filters.items() if value not in {"", "ALL", "25"}}
            query["page"] = max(1, target)
            return "/servicos/notas?" + urlencode(query)

        return templates.TemplateResponse(
            request,
            "notes/list.html",
            _context(
                request,
                session,
                "notas",
                result=result,
                filters=filters,
                customers=service.filter_customers(selected_id=parsed_customer),
                operational_statuses=OPERATIONAL_STATUS_LABELS,
                financial_statuses=FINANCIAL_STATUS_LABELS,
                deadline_statuses=DEADLINE_OPTIONS,
                page_url=page_url,
                page_title="Notas de Serviço",
            ),
        )


@router.get("/notas/{note_id}")
def note_detail(
    request: Request,
    note_id: int,
    saved: int = 0,
    updated: int = 0,
    status_changed: int = 0,
    canceled: int = 0,
    payment_saved: int = 0,
):
    require_permission(request, "notes.view")
    with request.app.state.session_factory() as session:
        service = NoteService(session, runtime_timezone(request, session))
        try:
            profile = service.profile(note_id)
        except NoteNotFoundError:
            _not_found()
        return _detail_response(
            request,
            session,
            profile,
            saved=bool(saved),
            updated=bool(updated),
            status_changed=bool(status_changed),
            canceled=bool(canceled),
            payment_saved=bool(payment_saved),
            action_error="",
            cancel_error="",
        )


@router.get("/notas/{note_id}/editar")
def edit_note(request: Request, note_id: int):
    require_permission(request, "notes.edit")
    with request.app.state.session_factory() as session:
        timezone_name = runtime_timezone(request, session)
        service = NoteService(session, timezone_name)
        try:
            note = service.detail(note_id)
        except NoteNotFoundError:
            _not_found()
        if note.operational_status == "CANCELADO":
            raise HTTPException(status_code=409, detail="Notas canceladas são imutáveis.")
        if ReceivableRepository(session).closure(note.id) is not None:
            raise HTTPException(status_code=409, detail="Notas fechadas são imutáveis.")
        return _form_response(
            request,
            session,
            service,
            form=service.form_from_note(note),
            errors={},
            item_errors=[],
            editing=True,
            note=note,
        )


@router.post("/notas/{note_id}/editar")
async def update_note(request: Request, note_id: int):
    user = require_permission(request, "notes.edit")
    raw = await request.form()
    with request.app.state.session_factory() as session:
        timezone_name = runtime_timezone(request, session)
        service = NoteService(session, timezone_name)
        try:
            current = service.detail(note_id)
        except NoteNotFoundError:
            _not_found()
        if current.financial_status == "PAGO":
            try:
                service.update_paid_notes(
                    note_id,
                    raw.get("notes"),
                    user.id,
                    {str(key) for key in raw.keys()},
                    raw.get("revision"),
                )
            except (NoteStateError, NoteConflictError, ValueError) as exc:
                return _form_response(
                    request,
                    session,
                    service,
                    form=service.form_from_note(current),
                    errors={"form": str(exc)},
                    item_errors=[],
                    editing=True,
                    note=current,
                    status_code=(
                        status.HTTP_409_CONFLICT
                        if isinstance(exc, (NoteStateError, NoteConflictError))
                        else status.HTTP_422_UNPROCESSABLE_CONTENT
                    ),
                )
            return RedirectResponse(f"/servicos/notas/{note_id}?updated=1", status_code=303)

        data = NoteInput.from_form(raw, timezone_name)
        response_status = status.HTTP_422_UNPROCESSABLE_CONTENT
        try:
            service.update(note_id, data, user.id)
        except NoteNotFoundError:
            _not_found()
        except NoteValidationError as exc:
            data = exc.data
        except DuplicateNoteNumberError:
            data.errors["number_series"] = "Já existe uma Nota com este número e esta série/bloco."
            return _form_response(
                request,
                session,
                service,
                form=data.as_form(timezone_name),
                errors=data.errors,
                item_errors=data.item_errors,
                editing=True,
                note=current,
                status_code=status.HTTP_409_CONFLICT,
            )
        except NoteConflictError as exc:
            data.errors["form"] = str(exc)
            response_status = status.HTTP_409_CONFLICT
        except NoteStateError as exc:
            data.errors["form"] = str(exc)
        else:
            return RedirectResponse(f"/servicos/notas/{note_id}?updated=1", status_code=303)
        return _form_response(
            request,
            session,
            service,
            form=data.as_form(timezone_name),
            errors=data.errors,
            item_errors=data.item_errors,
            editing=True,
            note=current,
            status_code=response_status,
        )


@router.post("/notas/{note_id}/status")
async def change_note_status(request: Request, note_id: int):
    user = require_permission(request, "notes.change_status")
    raw = await request.form()
    unexpected = {str(key) for key in raw.keys()} - {"target_status", "revision", "submit"}
    with request.app.state.session_factory() as session:
        service = NoteService(session, runtime_timezone(request, session))
        try:
            if unexpected:
                raise NoteStateError("O payload da alteração de status contém campos não permitidos.")
            service.change_status(
                note_id, raw.get("target_status"), user.id, raw.get("revision")
            )
        except NoteNotFoundError:
            _not_found()
        except (NoteStateError, NoteConflictError) as exc:
            try:
                profile = service.profile(note_id)
            except NoteNotFoundError:
                _not_found()
            return _detail_response(
                request,
                session,
                profile,
                action_error=str(exc),
                cancel_error="",
                saved=False,
                updated=False,
                status_changed=False,
                canceled=False,
                status_code=status.HTTP_409_CONFLICT,
            )
    return RedirectResponse(f"/servicos/notas/{note_id}?status_changed=1", status_code=303)


@router.post("/notas/{note_id}/cancelar")
async def cancel_note(request: Request, note_id: int):
    user = require_permission(request, "notes.cancel")
    raw = await request.form()
    unexpected = {str(key) for key in raw.keys()} - {"reason", "revision", "submit"}
    with request.app.state.session_factory() as session:
        service = NoteService(session, runtime_timezone(request, session))
        try:
            if unexpected:
                raise ValueError("O payload do cancelamento contém campos não permitidos.")
            service.cancel(note_id, raw.get("reason"), user.id, raw.get("revision"))
        except NoteNotFoundError:
            _not_found()
        except (NoteStateError, NoteConflictError, ValueError) as exc:
            try:
                profile = service.profile(note_id)
            except NoteNotFoundError:
                _not_found()
            return _detail_response(
                request,
                session,
                profile,
                action_error="",
                cancel_error=str(exc),
                saved=False,
                updated=False,
                status_changed=False,
                canceled=False,
                status_code=(
                    status.HTTP_409_CONFLICT
                    if isinstance(exc, (NoteStateError, NoteConflictError))
                    else status.HTTP_422_UNPROCESSABLE_CONTENT
                ),
            )
    return RedirectResponse(f"/servicos/notas/{note_id}?canceled=1", status_code=303)
