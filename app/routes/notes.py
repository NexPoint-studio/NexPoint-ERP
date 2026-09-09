from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import RedirectResponse

from app.core.modules import MODULE_BY_ID
from app.core.note_config import (
    DEADLINE_STATUS_LABELS,
    FINANCIAL_STATUS_LABELS,
    OPERATIONAL_STATUS_LABELS,
)
from app.core.permissions import require_permission
from app.routes.helpers import navigation_context, templates
from app.services.cash_validation import local_now
from app.services.note_validation import NoteInput
from app.services.notes import (
    DuplicateNoteNumberError,
    NoteConflictError,
    NoteNotFoundError,
    NoteService,
    NoteStateError,
    NoteValidationError,
)


router = APIRouter(prefix="/servicos")


DEADLINE_OPTIONS = {
    "OVERDUE": DEADLINE_STATUS_LABELS["ATRASADO"],
    "TODAY": DEADLINE_STATUS_LABELS["VENCE_HOJE"],
}
EVENT_TYPE_LABELS = {
    "NOTE_CREATED": "Nota criada",
    "NOTE_UPDATED": "Nota atualizada",
    "STATUS_CHANGED": "Status alterado",
    "NOTE_CANCELLED": "Nota cancelada",
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
    }


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
    status_code: int = 200,
):
    customers = service.form_customers()
    if note is not None and all(customer.id != note.customer.id for customer in customers):
        customers.append(note.customer)
        customers.sort(key=lambda customer: (customer.name.casefold(), customer.id))
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
            page_title=(f"Editar Nota {note.number_original}" if editing else "Nova Nota de Serviço"),
        ),
        status_code=status_code,
    )


def _detail_response(request: Request, session, profile, *, status_code: int = 200, **extra):
    return templates.TemplateResponse(
        request,
        "notes/detail.html",
        _context(
            request,
            session,
            "notas",
            profile=profile,
            page_title=f"Nota {profile.note.number_original}",
            **extra,
        ),
        status_code=status_code,
    )


@router.get("/nova-nota")
def new_note(request: Request):
    require_permission(request, "notes.create")
    timezone_name = request.app.state.settings.timezone
    with request.app.state.session_factory() as session:
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
    timezone_name = request.app.state.settings.timezone
    raw = await request.form()
    data = NoteInput.from_form(raw, timezone_name)
    with request.app.state.session_factory() as session:
        service = NoteService(session, timezone_name)
        try:
            note = service.create(data, user.id)
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
                editing=False,
                status_code=status.HTTP_409_CONFLICT,
            )
        else:
            return RedirectResponse(f"/servicos/notas/{note.id}?saved=1", status_code=303)
        return _form_response(
            request,
            session,
            service,
            form=data.as_form(timezone_name),
            errors=data.errors,
            item_errors=data.item_errors,
            editing=False,
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )


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
    timezone_name = request.app.state.settings.timezone
    with request.app.state.session_factory() as session:
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
                customers=service.filter_customers(),
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
):
    require_permission(request, "notes.view")
    with request.app.state.session_factory() as session:
        service = NoteService(session, request.app.state.settings.timezone)
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
            action_error="",
            cancel_error="",
        )


@router.get("/notas/{note_id}/editar")
def edit_note(request: Request, note_id: int):
    require_permission(request, "notes.edit")
    timezone_name = request.app.state.settings.timezone
    with request.app.state.session_factory() as session:
        service = NoteService(session, timezone_name)
        try:
            note = service.detail(note_id)
        except NoteNotFoundError:
            _not_found()
        if note.operational_status == "CANCELADO":
            raise HTTPException(status_code=409, detail="Notas canceladas são imutáveis.")
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
    timezone_name = request.app.state.settings.timezone
    raw = await request.form()
    with request.app.state.session_factory() as session:
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
        service = NoteService(session, request.app.state.settings.timezone)
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
        service = NoteService(session, request.app.state.settings.timezone)
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
