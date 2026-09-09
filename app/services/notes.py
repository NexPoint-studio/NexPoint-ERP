"""Regras operacionais, cálculos e transações das Notas de Serviço."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Context, Decimal, InvalidOperation, localcontext
import json
import math
from uuid import uuid4

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.money import MAX_CENTS, cents_to_decimal, decimal_to_cents
from app.core.note_config import (
    MAX_NOTE_NOTES_LENGTH,
    MAX_SCALED_QUANTITY,
    OPERATIONAL_TRANSITIONS,
    DeadlineInfo,
    deadline_info,
)
from app.models import AuditEvent, ServiceNote, ServiceNoteEvent, ServiceNoteItem
from app.repositories.customers import CustomerRepository
from app.repositories.notes import AvailableService, NoteRepository
from app.services.note_validation import (
    NoteInput,
    NoteItemInput,
    canonical_quantity,
    format_local_datetime,
    validate_quantity,
)
from app.services.cash_validation import project_zone
from app.services.transactions import atomic_write


class NoteValidationError(Exception):
    def __init__(self, data: NoteInput):
        self.data = data


class NoteNotFoundError(Exception):
    pass


class DuplicateNoteNumberError(Exception):
    pass


class NoteStateError(Exception):
    pass


class NoteConflictError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class ResolvedItem:
    input: NoteItemInput
    existing: ServiceNoteItem | None
    service_id: int
    service_code: str | None
    service_name: str
    service_description: str | None
    service_category_name: str | None
    billing_unit_id: int
    billing_unit_code: str
    billing_unit_name: str
    billing_unit_symbol: str
    quantity_behavior: str
    decimal_places: int
    quantity: Decimal
    quantity_scaled: int
    unit_price_cents: int
    subtotal_cents: int


@dataclass(frozen=True, slots=True)
class NoteCalculation:
    items: tuple[ResolvedItem, ...]
    services_subtotal_cents: int
    discount_type: str | None
    discount_input: str | None
    discount_base_cents: int
    discount_amount_cents: int
    delivery_amount_cents: int
    total_cents: int
    financial_status: str
    financial_settlement_reason: str | None


@dataclass(frozen=True, slots=True)
class NoteListRow:
    note: ServiceNote
    deadline: DeadlineInfo

    @property
    def customer(self):
        return self.note.customer

    @property
    def deadline_code(self) -> str:
        return self.deadline.code

    @property
    def deadline_label(self) -> str:
        return self.deadline.label

    @property
    def days_late(self) -> int:
        return self.deadline.days_late


@dataclass(frozen=True, slots=True)
class NoteListResult:
    rows: list[NoteListRow]
    total: int
    page: int
    per_page: int
    pages: int


@dataclass(frozen=True, slots=True)
class NoteProfile:
    note: ServiceNote
    actor_names: dict[int, str]
    deadline: DeadlineInfo
    allowed_transitions: tuple[str, ...]

    @property
    def deadline_code(self) -> str:
        return self.deadline.code

    @property
    def deadline_label(self) -> str:
        return self.deadline.label

    @property
    def days_late(self) -> int:
        return self.deadline.days_late

    @property
    def production_done(self) -> bool:
        return self.deadline.production_done

    @property
    def items(self):
        return self.note.items

    @property
    def events(self):
        return self.note.events


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _scale_quantity(quantity: Decimal, decimal_places: int) -> int:
    context = Context(prec=40)
    try:
        scaled = context.scaleb(quantity, decimal_places)
    except InvalidOperation:
        raise ValueError("A quantidade excede o limite permitido.") from None
    if scaled != scaled.to_integral_value() or scaled <= 0 or scaled > MAX_SCALED_QUANTITY:
        raise ValueError("A quantidade excede o limite permitido.")
    return int(scaled)


def _item_subtotal(quantity: Decimal, unit_price_cents: int) -> int:
    if not isinstance(unit_price_cents, int) or isinstance(unit_price_cents, bool) or not 0 <= unit_price_cents <= MAX_CENTS:
        raise ValueError("O preço vigente do serviço é inválido.")
    with localcontext(Context(prec=50)) as context:
        amount = context.multiply(quantity, cents_to_decimal(unit_price_cents))
    cents = decimal_to_cents(amount)
    if cents < 0:
        raise ValueError("O subtotal do item é inválido.")
    return cents


def _safe_cents_sum(values: list[int], message: str) -> int:
    total = sum(values)
    if not 0 <= total <= MAX_CENTS:
        raise ValueError(message)
    return total


def _canonical_decimal(value: Decimal) -> str:
    return canonical_quantity(value)


class NoteService:
    def __init__(self, session: Session, timezone_name: str):
        self.session = session
        self.timezone_name = timezone_name
        self.repository = NoteRepository(session)
        self.customers = CustomerRepository(session)

    def _audit(self, user_id: int, action: str, note_id: int, details: dict | None = None) -> None:
        self.session.add(AuditEvent(
            user_id=user_id,
            action=action,
            resource=f"service_notes/{note_id}",
            details=json.dumps(details, ensure_ascii=False, sort_keys=True, default=str) if details else None,
        ))

    def _event(self, note_id: int, event_type: str, user_id: int, details: dict | None = None, *, at: datetime | None = None) -> None:
        self.session.add(ServiceNoteEvent(
            event_uid=str(uuid4()),
            note_id=note_id,
            event_type=event_type,
            occurred_at=at or _utc_now(),
            created_by=user_id,
            details_json=json.dumps(details, ensure_ascii=False, sort_keys=True, default=str) if details else None,
        ))

    @staticmethod
    def _customer_activity_reference(note: ServiceNote) -> str:
        reference = f"Nota {note.number_original}"
        if note.series_original:
            reference += f" · Série {note.series_original}"
        return reference[:180]

    @staticmethod
    def _customer_activity_metadata(
        note: ServiceNote,
        *,
        operational_status: str,
    ) -> dict[str, object]:
        return {
            "service_note_id": note.id,
            "number_snapshot": note.number_original,
            "series_snapshot": note.series_original,
            "operational_status": operational_status,
            "financial_status": note.financial_status,
            "total_cents": note.total_cents,
            "items": [
                {
                    "service_id": item.service_id,
                    "service_code_snapshot": item.service_code_snapshot,
                    "service_name_snapshot": item.service_name_snapshot,
                    "billing_unit_code_snapshot": item.billing_unit_code_snapshot,
                    "billing_unit_name_snapshot": item.billing_unit_name_snapshot,
                    "billing_unit_symbol_snapshot": item.billing_unit_symbol_snapshot,
                    "quantity_behavior_snapshot": item.quantity_behavior_snapshot,
                    "decimal_places_snapshot": item.decimal_places_snapshot,
                    "quantity_scaled": item.quantity_scaled,
                    "unit_price_cents": item.unit_price_cents,
                    "subtotal_cents": item.subtotal_cents,
                }
                for item in sorted(note.items, key=lambda row: row.position)
            ],
        }

    def _customer_activity(
        self,
        note: ServiceNote,
        activity_type: str,
        user_id: int,
        *,
        occurred_at: datetime,
        operational_status: str,
    ) -> None:
        customer = self.customers.get(note.customer_id)
        if customer is None:
            raise NoteStateError("O cliente da Nota não foi encontrado.")
        reference = self._customer_activity_reference(note)
        service_names = ", ".join(
            item.service_name_snapshot
            for item in sorted(note.items, key=lambda row: row.position)
        )
        if activity_type == "SERVICE_CREATED":
            description = f"{reference} criada"
        elif activity_type == "SERVICE_COMPLETED":
            description = f"Serviços da {reference} concluídos"
        else:
            raise ValueError("Tipo de atividade automática da Nota inválido.")
        if service_names:
            description += f": {service_names}"
        description = f"{description}."[:300]
        self.customers.add_activity(
            customer,
            activity_type,
            description,
            user_id,
            occurred_at=occurred_at,
            metadata_json=json.dumps(
                self._customer_activity_metadata(
                    note,
                    operational_status=operational_status,
                ),
                ensure_ascii=False,
                sort_keys=True,
            ),
            source_type="SERVICE_NOTE",
            source_id=str(note.id),
            source_reference=reference,
        )

    @staticmethod
    def _is_number_conflict(exc: IntegrityError) -> bool:
        text = str(getattr(exc, "orig", exc)).casefold()
        return (
            "service_notes.number_normalized, service_notes.series_normalized" in text
            or "uq_service_notes_number_series" in text
        )

    def _commit(self) -> None:
        try:
            self.session.commit()
        except IntegrityError as exc:
            self.session.rollback()
            if self._is_number_conflict(exc):
                raise DuplicateNoteNumberError() from None
            raise

    @staticmethod
    def _expected_revision(note: ServiceNote, raw_revision: object) -> int:
        raw = str(raw_revision or "").strip()
        if not raw.isascii() or not raw.isdecimal() or len(raw) > 19:
            raise NoteConflictError("A Nota foi alterada. Atualize a página e tente novamente.")
        expected = int(raw)
        if expected != note.revision:
            raise NoteConflictError("A Nota foi alterada. Atualize a página e tente novamente.")
        return expected

    def _claim_revision(self, note: ServiceNote, expected: int) -> None:
        claimed = self.session.execute(
            update(ServiceNote)
            .where(ServiceNote.id == note.id, ServiceNote.revision == expected)
            .values(revision=expected + 1)
            .execution_options(synchronize_session=False)
        )
        if claimed.rowcount != 1:
            raise NoteConflictError("A Nota foi alterada em outra solicitação. Atualize a página.")
        note.revision = expected + 1

    @staticmethod
    def _source_from_existing(item: ServiceNoteItem) -> dict[str, object]:
        return {
            "service_id": item.service_id,
            "service_code": item.service_code_snapshot,
            "service_name": item.service_name_snapshot,
            "service_description": item.service_description_snapshot,
            "service_category_name": item.service_category_name_snapshot,
            "billing_unit_id": item.billing_unit_id,
            "billing_unit_code": item.billing_unit_code_snapshot,
            "billing_unit_name": item.billing_unit_name_snapshot,
            "billing_unit_symbol": item.billing_unit_symbol_snapshot,
            "quantity_behavior": item.quantity_behavior_snapshot,
            "decimal_places": item.decimal_places_snapshot,
            "unit_price_cents": item.unit_price_cents,
        }

    @staticmethod
    def _source_from_catalog(service: AvailableService) -> dict[str, object]:
        unit = service.billing_unit
        return {
            "service_id": service.id,
            "service_code": service.code,
            "service_name": service.name,
            "service_description": service.description,
            "service_category_name": service.category_name,
            "billing_unit_id": unit.id,
            "billing_unit_code": unit.code,
            "billing_unit_name": unit.name,
            "billing_unit_symbol": unit.symbol,
            "quantity_behavior": unit.quantity_behavior,
            "decimal_places": unit.decimal_places,
            "unit_price_cents": service.current_price_cents,
        }

    def _resolve_items(self, data: NoteInput, note: ServiceNote | None) -> list[ResolvedItem]:
        existing_by_id = {item.id: item for item in note.items} if note is not None else {}
        resolved: list[ResolvedItem] = []
        for item_input in data.items:
            if item_input.errors:
                continue
            existing = None
            source: dict[str, object] | None = None
            if item_input.item_id is not None:
                existing = existing_by_id.get(item_input.item_id)
                if existing is None:
                    item_input.errors["item_id"] = "Este item não pertence à Nota."
                    continue
                if item_input.service_id != existing.service_id:
                    item_input.errors["service_id"] = "O serviço de um item existente não pode ser substituído."
                    continue
                source = self._source_from_existing(existing)
            elif item_input.service_id is not None:
                available = self.repository.available_service(item_input.service_id, active_only=True)
                if available is None:
                    item_input.errors["service_id"] = "Serviço indisponível ou sem preço vigente."
                    continue
                source = self._source_from_catalog(available)
            if source is None:
                item_input.errors.setdefault("service_id", "Selecione um serviço válido.")
                continue
            try:
                quantity = validate_quantity(
                    item_input.quantity_text,
                    str(source["quantity_behavior"]),
                    int(source["decimal_places"]),
                )
                quantity_scaled = _scale_quantity(quantity, int(source["decimal_places"]))
                subtotal_cents = _item_subtotal(quantity, int(source["unit_price_cents"]))
            except (TypeError, ValueError, OverflowError) as exc:
                item_input.errors["quantity"] = str(exc)
                continue
            resolved.append(ResolvedItem(
                input=item_input,
                existing=existing,
                service_id=int(source["service_id"]),
                service_code=source["service_code"] if source["service_code"] is None else str(source["service_code"]),
                service_name=str(source["service_name"]),
                service_description=source["service_description"] if source["service_description"] is None else str(source["service_description"]),
                service_category_name=source["service_category_name"] if source["service_category_name"] is None else str(source["service_category_name"]),
                billing_unit_id=int(source["billing_unit_id"]),
                billing_unit_code=str(source["billing_unit_code"]),
                billing_unit_name=str(source["billing_unit_name"]),
                billing_unit_symbol=str(source["billing_unit_symbol"]),
                quantity_behavior=str(source["quantity_behavior"]),
                decimal_places=int(source["decimal_places"]),
                quantity=quantity,
                quantity_scaled=quantity_scaled,
                unit_price_cents=int(source["unit_price_cents"]),
                subtotal_cents=subtotal_cents,
            ))
        if any(item.errors for item in data.items):
            data.errors["items"] = "Revise os itens destacados."
        return resolved

    def _calculate(self, data: NoteInput, note: ServiceNote | None = None) -> NoteCalculation:
        if data.customer_id is not None:
            customer = self.repository.customer(data.customer_id)
            if customer is None:
                data.errors["customer_id"] = "Cliente não encontrado."
            elif not customer.is_active and (note is None or customer.id != note.customer_id):
                data.errors["customer_id"] = "Selecione um cliente ativo."
        resolved = self._resolve_items(data, note)
        if data.errors:
            raise NoteValidationError(data)
        try:
            services_subtotal = _safe_cents_sum(
                [item.subtotal_cents for item in resolved],
                "O subtotal dos serviços excede o limite permitido.",
            )
        except ValueError as exc:
            data.errors["items"] = str(exc)
            raise NoteValidationError(data) from None

        try:
            delivery_cents = decimal_to_cents(data.delivery_amount) if data.delivery_enabled else 0
            if delivery_cents < 0:
                raise ValueError()
        except (TypeError, ValueError, OverflowError):
            data.errors["delivery_amount"] = "O valor da entrega excede o limite permitido."
            raise NoteValidationError(data) from None

        discount_base = services_subtotal
        discount_cents = 0
        discount_input = None
        if data.discount_type == "VALOR":
            base_decimal = cents_to_decimal(services_subtotal)
            if data.discount_input > base_decimal:
                data.errors["discount_input"] = "O desconto não pode ser maior que o subtotal dos serviços."
                raise NoteValidationError(data)
            try:
                discount_cents = decimal_to_cents(data.discount_input)
            except (TypeError, ValueError, OverflowError):
                data.errors["discount_input"] = "O desconto excede o limite permitido."
                raise NoteValidationError(data) from None
            discount_input = _canonical_decimal(data.discount_input)
        elif data.discount_type == "PERCENTUAL":
            with localcontext(Context(prec=50)) as context:
                raw_discount = context.divide(
                    context.multiply(cents_to_decimal(services_subtotal), data.discount_input),
                    Decimal("100"),
                )
            discount_cents = decimal_to_cents(raw_discount)
            discount_input = _canonical_decimal(data.discount_input)
        if discount_cents > services_subtotal:
            data.errors["discount_input"] = "O desconto não pode ser maior que o subtotal dos serviços."
            raise NoteValidationError(data)

        try:
            total = _safe_cents_sum(
                [services_subtotal - discount_cents, delivery_cents],
                "O total da Nota excede o limite permitido.",
            )
        except ValueError as exc:
            data.errors["form"] = str(exc)
            raise NoteValidationError(data) from None
        financial_status = "PAGO" if total == 0 else "PENDENTE"
        return NoteCalculation(
            tuple(resolved), services_subtotal, data.discount_type, discount_input,
            discount_base, discount_cents, delivery_cents, total,
            financial_status, "ZERO_TOTAL" if total == 0 else None,
        )

    def _check_duplicate(self, data: NoteInput, *, exclude_id: int | None = None) -> None:
        if self.repository.by_number(data.number_normalized, data.series_normalized, exclude_id=exclude_id):
            raise DuplicateNoteNumberError()

    @atomic_write
    def create(self, data: NoteInput, user_id: int) -> ServiceNote:
        if data.revision is not None:
            data.errors["form"] = "A versão da Nota não pode ser definida no cadastro."
        calculation = self._calculate(data)
        self._check_duplicate(data)
        note = ServiceNote(
            number_original=data.number,
            number_normalized=data.number_normalized,
            series_original=data.series or None,
            series_normalized=data.series_normalized,
            customer_id=data.customer_id,
            received_at=data.received_at,
            expected_ready_at=data.expected_ready_at,
            operational_status="RECEBIDO",
            financial_status=calculation.financial_status,
            financial_settlement_reason=calculation.financial_settlement_reason,
            delivery_enabled=data.delivery_enabled,
            delivery_amount_cents=calculation.delivery_amount_cents,
            discount_type=calculation.discount_type,
            discount_input=calculation.discount_input,
            discount_base_cents=calculation.discount_base_cents,
            discount_amount_cents=calculation.discount_amount_cents,
            services_subtotal_cents=calculation.services_subtotal_cents,
            total_cents=calculation.total_cents,
            notes=data.notes,
            created_by=user_id,
            updated_by=user_id,
        )
        self.session.add(note)
        try:
            self.session.flush()
        except IntegrityError as exc:
            self.session.rollback()
            if self._is_number_conflict(exc):
                raise DuplicateNoteNumberError() from None
            raise
        for position, item in enumerate(calculation.items, start=1):
            note.items.append(self._new_item(item, position))
        self._event(note.id, "NOTE_CREATED", user_id, {
            "operational_status": note.operational_status,
            "financial_status": note.financial_status,
            "item_count": len(calculation.items),
        })
        self._customer_activity(
            note,
            "SERVICE_CREATED",
            user_id,
            occurred_at=note.received_at,
            operational_status=note.operational_status,
        )
        self._audit(user_id, "service_note.created", note.id, {
            "number": note.number_original,
            "series": note.series_original,
            "total_cents": note.total_cents,
        })
        self._commit()
        return note

    @staticmethod
    def _new_item(item: ResolvedItem, position: int) -> ServiceNoteItem:
        return ServiceNoteItem(
            service_id=item.service_id,
            service_code_snapshot=item.service_code,
            service_name_snapshot=item.service_name,
            service_description_snapshot=item.service_description,
            service_category_name_snapshot=item.service_category_name,
            billing_unit_id=item.billing_unit_id,
            billing_unit_code_snapshot=item.billing_unit_code,
            billing_unit_name_snapshot=item.billing_unit_name,
            billing_unit_symbol_snapshot=item.billing_unit_symbol,
            quantity_behavior_snapshot=item.quantity_behavior,
            decimal_places_snapshot=item.decimal_places,
            quantity_scaled=item.quantity_scaled,
            unit_price_cents=item.unit_price_cents,
            subtotal_cents=item.subtotal_cents,
            position=position,
        )

    @staticmethod
    def _record_change(changes: set[str], field_name: str, before, after) -> None:
        if before != after:
            changes.add(field_name)

    @atomic_write
    def update(self, note_id: int, data: NoteInput, user_id: int) -> ServiceNote:
        note = self.repository.get(note_id)
        if note is None:
            raise NoteNotFoundError()
        if note.operational_status == "CANCELADO":
            raise NoteStateError("Notas canceladas são imutáveis.")
        if note.financial_status == "PAGO":
            raise NoteStateError("Notas pagas permitem alterar somente as observações.")
        if note.ready_at is not None and (
            data.received_at != note.received_at or data.expected_ready_at != note.expected_ready_at
        ):
            raise NoteStateError("As datas de produção não podem ser alteradas depois de marcar a Nota como pronta.")
        calculation = self._calculate(data, note)
        self._check_duplicate(data, exclude_id=note.id)
        expected_revision = self._expected_revision(note, data.revision)
        self._claim_revision(note, expected_revision)
        changes: set[str] = set()
        scalar_values = {
            "number_original": data.number,
            "number_normalized": data.number_normalized,
            "series_original": data.series or None,
            "series_normalized": data.series_normalized,
            "customer_id": data.customer_id,
            "received_at": data.received_at,
            "expected_ready_at": data.expected_ready_at,
            "delivery_enabled": data.delivery_enabled,
            "delivery_amount_cents": calculation.delivery_amount_cents,
            "discount_type": calculation.discount_type,
            "discount_input": calculation.discount_input,
            "discount_base_cents": calculation.discount_base_cents,
            "discount_amount_cents": calculation.discount_amount_cents,
            "services_subtotal_cents": calculation.services_subtotal_cents,
            "total_cents": calculation.total_cents,
            "financial_status": calculation.financial_status,
            "financial_settlement_reason": calculation.financial_settlement_reason,
            "notes": data.notes,
        }
        for field_name, after in scalar_values.items():
            before = getattr(note, field_name)
            self._record_change(changes, field_name, before, after)
            setattr(note, field_name, after)

        retained_ids = {item.existing.id for item in calculation.items if item.existing is not None}
        removed_items = False
        for old_item in list(note.items):
            if old_item.id not in retained_ids:
                note.items.remove(old_item)
                changes.add("items")
                removed_items = True
        if removed_items:
            # Libera as posições dos itens excluídos antes de compactar a ordem.
            self.session.flush()

        positioned_existing = [
            (position, resolved.existing)
            for position, resolved in enumerate(calculation.items, start=1)
            if resolved.existing is not None
        ]
        if any(item.position != position for position, item in positioned_existing):
            # A unicidade (note_id, position) é imediata no SQLite. Mover primeiro
            # para posições temporárias evita colisão em trocas e compactações.
            occupied = {item.position for _, item in positioned_existing}
            temporary = [value for value in range(9999, -1, -1) if value not in occupied]
            if len(temporary) < len(positioned_existing):
                raise NoteConflictError("Não foi possível reorganizar os itens da Nota.")
            for (_, item), temporary_position in zip(
                positioned_existing,
                temporary[:len(positioned_existing)],
                strict=True,
            ):
                item.position = temporary_position
            self.session.flush()

        for position, resolved in enumerate(calculation.items, start=1):
            if resolved.existing is None:
                note.items.append(self._new_item(resolved, position))
                changes.add("items")
                continue
            item = resolved.existing
            if item.quantity_scaled != resolved.quantity_scaled or item.subtotal_cents != resolved.subtotal_cents or item.position != position:
                changes.add("items")
            item.quantity_scaled = resolved.quantity_scaled
            item.subtotal_cents = resolved.subtotal_cents
            item.position = position
        if not changes:
            self.session.rollback()
            return note
        note.updated_by = user_id
        note.updated_at = _utc_now()
        self._event(note.id, "NOTE_UPDATED", user_id, {
            "fields": sorted(changes),
            "item_count": len(calculation.items),
        })
        self._audit(user_id, "service_note.updated", note.id, {"fields": sorted(changes)})
        self._commit()
        return note

    @atomic_write
    def update_paid_notes(
        self,
        note_id: int,
        notes: object,
        user_id: int,
        submitted_fields: set[str],
        expected_revision: object,
    ) -> ServiceNote:
        note = self.repository.get(note_id)
        if note is None:
            raise NoteNotFoundError()
        if note.operational_status == "CANCELADO":
            raise NoteStateError("Notas canceladas são imutáveis.")
        if note.financial_status != "PAGO":
            raise NoteStateError("Use a edição completa para uma Nota pendente.")
        unexpected = {
            field for field in submitted_fields
            if field not in {"notes", "revision", "submit"} and not field.startswith("_")
        }
        if unexpected:
            raise NoteStateError("Campos financeiros e cadastrais de uma Nota paga não podem ser alterados.")
        clean_notes = str(notes or "").strip()
        if len(clean_notes) > MAX_NOTE_NOTES_LENGTH:
            raise ValueError(f"As observações devem ter no máximo {MAX_NOTE_NOTES_LENGTH} caracteres.")
        clean_notes = clean_notes or None
        revision = self._expected_revision(note, expected_revision)
        if note.notes == clean_notes:
            return note
        self._claim_revision(note, revision)
        note.notes = clean_notes
        note.updated_by = user_id
        note.updated_at = _utc_now()
        self._event(note.id, "NOTE_UPDATED", user_id, {"fields": ["notes"]})
        self._audit(user_id, "service_note.updated", note.id, {"fields": ["notes"]})
        self._commit()
        return note

    @atomic_write
    def change_status(
        self,
        note_id: int,
        target_status: object,
        user_id: int,
        expected_revision: object,
    ) -> ServiceNote:
        note = self.repository.get(note_id)
        if note is None:
            raise NoteNotFoundError()
        target = str(target_status or "").strip().upper()
        allowed = OPERATIONAL_TRANSITIONS.get(note.operational_status, ())
        if target not in allowed:
            raise NoteStateError("Esta transição de status não é permitida.")
        revision = self._expected_revision(note, expected_revision)
        now = _utc_now()
        values: dict[str, object] = {
            "operational_status": target,
            "revision": revision + 1,
            "updated_by": user_id,
            "updated_at": now,
        }
        if target == "PRONTO":
            ready_deadline = deadline_info(note, self.timezone_name, now=now)
            values["ready_at"] = now
            values["ready_delay_days"] = ready_deadline.days_late
        before = note.operational_status
        persistent_id = note.id
        changed = self.session.execute(
            update(ServiceNote)
            .where(
                ServiceNote.id == note.id,
                ServiceNote.revision == revision,
                ServiceNote.operational_status == note.operational_status,
            )
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        if changed.rowcount != 1:
            raise NoteConflictError("A Nota foi alterada em outra solicitação. Atualize a página.")
        self._event(persistent_id, "STATUS_CHANGED", user_id, {"from": before, "to": target}, at=now)
        if target == "PRONTO":
            self._customer_activity(
                note,
                "SERVICE_COMPLETED",
                user_id,
                occurred_at=now,
                operational_status=target,
            )
        self._audit(user_id, "service_note.status_changed", persistent_id, {"from": before, "to": target})
        self._commit()
        refreshed = self.repository.get(persistent_id)
        if refreshed is None:
            raise NoteNotFoundError()
        return refreshed

    @atomic_write
    def cancel(
        self,
        note_id: int,
        reason: object,
        user_id: int,
        expected_revision: object,
    ) -> ServiceNote:
        note = self.repository.get(note_id)
        if note is None:
            raise NoteNotFoundError()
        clean_reason = str(reason or "").strip()
        if not clean_reason:
            raise ValueError("Informe o motivo do cancelamento.")
        if len(clean_reason) > 500:
            raise ValueError("O motivo deve ter no máximo 500 caracteres.")
        if note.operational_status == "CANCELADO":
            raise NoteStateError("A Nota já está cancelada.")
        revision = self._expected_revision(note, expected_revision)
        now = _utc_now()
        before = note.operational_status
        persistent_id = note.id
        changed = self.session.execute(
            update(ServiceNote)
            .where(
                ServiceNote.id == note.id,
                ServiceNote.revision == revision,
                ServiceNote.operational_status == before,
            )
            .values(
                operational_status="CANCELADO",
                revision=revision + 1,
                canceled_at=now,
                canceled_by=user_id,
                cancellation_reason=clean_reason,
                updated_by=user_id,
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        if changed.rowcount != 1:
            raise NoteConflictError("A Nota foi alterada em outra solicitação. Atualize a página.")
        self._event(persistent_id, "NOTE_CANCELLED", user_id, {"from": before, "reason": clean_reason}, at=now)
        self._audit(user_id, "service_note.cancelled", persistent_id, {"from": before, "reason": clean_reason})
        self._commit()
        refreshed = self.repository.get(persistent_id)
        if refreshed is None:
            raise NoteNotFoundError()
        return refreshed

    def detail(self, note_id: int) -> ServiceNote:
        note = self.repository.get(note_id)
        if note is None:
            raise NoteNotFoundError()
        return note

    def profile(self, note_id: int) -> NoteProfile:
        note = self.detail(note_id)
        return NoteProfile(
            note,
            self.repository.actor_names(note),
            deadline_info(note, self.timezone_name),
            OPERATIONAL_TRANSITIONS.get(note.operational_status, ()),
        )

    def available_services(self) -> list[AvailableService]:
        return self.repository.available_services()

    def form_customers(self):
        return self.repository.customers(active_only=True)

    def filter_customers(self):
        return self.repository.customers(active_only=False)

    def form_from_note(self, note: ServiceNote) -> dict[str, object]:
        discount_input = note.discount_input if note.discount_type else ""
        return {
            "number": note.number_original,
            "series": note.series_original or "",
            "customer_id": str(note.customer_id),
            "received_at": format_local_datetime(note.received_at, self.timezone_name),
            "expected_ready_at": format_local_datetime(note.expected_ready_at, self.timezone_name),
            "notes": note.notes or "",
            "delivery_enabled": "1" if note.delivery_enabled else "0",
            "delivery_amount": format(cents_to_decimal(note.delivery_amount_cents), "f") if note.delivery_enabled else "",
            "discount_type": note.discount_type or "",
            "discount_input": discount_input,
            "revision": str(note.revision),
            "items": [
                {
                    "item_id": str(item.id),
                    "service_id": str(item.service_id),
                    "quantity": canonical_quantity(item.quantity),
                    "snapshot": item,
                }
                for item in sorted(note.items, key=lambda row: row.position)
            ],
        }

    @staticmethod
    def _date_bounds(raw: str, timezone_name: str) -> tuple[datetime, datetime]:
        try:
            selected = date.fromisoformat(raw)
            zone = project_zone(timezone_name)
        except (ValueError, RuntimeError):
            raise ValueError("Informe um período válido.") from None
        start_local = datetime.combine(selected, time.min, zone)
        end_local = datetime.combine(selected + timedelta(days=1), time.min, zone)
        return (
            start_local.astimezone(timezone.utc).replace(tzinfo=None),
            end_local.astimezone(timezone.utc).replace(tzinfo=None),
        )

    def list(
        self,
        *,
        search: str = "",
        customer_id: int | None = None,
        operational_status: str = "ALL",
        financial_status: str = "ALL",
        deadline_status: str = "ALL",
        date_from: str = "",
        date_to: str = "",
        page: int = 1,
        per_page: int = 25,
    ) -> NoteListResult:
        if customer_id is not None and (not isinstance(customer_id, int) or not 0 < customer_id < 2**63):
            raise ValueError("Selecione um cliente válido.")
        if operational_status not in {"ALL", *OPERATIONAL_TRANSITIONS}:
            raise ValueError("Selecione um status operacional válido.")
        if financial_status not in {"ALL", "PENDENTE", "PAGO"}:
            raise ValueError("Selecione um status financeiro válido.")
        if deadline_status not in {"ALL", "OVERDUE", "TODAY"}:
            raise ValueError("Selecione uma situação de prazo válida.")
        if per_page not in {10, 25, 50, 100}:
            raise ValueError("Selecione uma paginação válida.")
        page = max(1, page)
        received_from = self._date_bounds(date_from, self.timezone_name)[0] if date_from else None
        received_to = self._date_bounds(date_to, self.timezone_name)[1] if date_to else None
        if received_from and received_to and received_from >= received_to:
            raise ValueError("A data inicial não pode ser posterior à data final.")
        try:
            zone = project_zone(self.timezone_name)
        except RuntimeError:
            raise ValueError("O fuso horário da empresa é inválido.") from None
        today = datetime.now(zone).date()
        today_start, tomorrow_start = self._date_bounds(today.isoformat(), self.timezone_name)
        kwargs = dict(
            search=search,
            customer_id=customer_id,
            operational_status=operational_status,
            financial_status=financial_status,
            deadline_status=deadline_status,
            received_from=received_from,
            received_to=received_to,
            today_start=today_start,
            tomorrow_start=tomorrow_start,
            per_page=per_page,
        )
        notes, total = self.repository.list(page=page, **kwargs)
        pages = max(1, math.ceil(total / per_page))
        if page > pages:
            page = pages
            notes, total = self.repository.list(page=page, **kwargs)
        return NoteListResult(
            [NoteListRow(note, deadline_info(note, self.timezone_name)) for note in notes],
            total, page, per_page, pages,
        )
