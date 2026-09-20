from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math

from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from app.services.transactions import atomic_write

from app.core.customer_config import InactivityThresholds, RELATIONSHIP_BADGES
from app.models import Customer, CustomerActivity
from app.repositories import ConfigurationRepository, CustomerActivityRepository, CustomerRepository
from app.repositories.customers import (
    ActivityItem,
    CustomerListItem,
    CustomerOption,
    LastServiceSummary,
)
from app.services.customer_validation import CustomerInput


ACTIVITY_LABELS = {
    "CUSTOMER_CREATED": "Cliente cadastrado",
    "CUSTOMER_UPDATED": "Cadastro atualizado",
    "CUSTOMER_DEACTIVATED": "Cliente inativado",
    "CUSTOMER_REACTIVATED": "Cliente reativado",
    "VISIT": "Visita registrada",
    "NOTE": "Observação adicionada",
    "SERVICE_CREATED": "Serviço criado",
    "SERVICE_COMPLETED": "Serviço concluído",
}


class CustomerValidationError(Exception):
    def __init__(self, data: CustomerInput):
        self.data = data


class DuplicateDocumentError(Exception):
    def __init__(self, customer: Customer):
        self.customer = customer


class CustomerNotFoundError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class PossibleDuplicate:
    customers: list[Customer]


@dataclass(frozen=True, slots=True)
class CustomerRow:
    customer: Customer
    last_activity: datetime | None
    relationship_code: str
    relationship_label: str
    relationship_badge: str
    inactive_days: int | None


@dataclass(frozen=True, slots=True)
class CustomerListResult:
    rows: list[CustomerRow]
    total: int
    page: int
    per_page: int
    pages: int
    indicators: dict[str, int]
    thresholds: InactivityThresholds


@dataclass(frozen=True, slots=True)
class CustomerHistoryResult:
    rows: list[ActivityItem]
    total: int
    page: int
    per_page: int
    pages: int


@dataclass(frozen=True, slots=True)
class CustomerProfile:
    customer: Customer
    last_activity: datetime | None
    relationship_code: str
    relationship_label: str
    relationship_badge: str
    inactive_days: int | None
    last_service: LastServiceSummary | None
    activities: list[CustomerActivity]
    actor_names: dict[int, str]
    activity_total: int
    activity_page: int
    activity_per_page: int
    activity_pages: int


class CustomerService:
    def __init__(self, session: Session):
        self.session = session
        self.repository = CustomerRepository(session)
        self.activities = CustomerActivityRepository(session)
        settings = ConfigurationRepository(session).settings()
        self.thresholds = InactivityThresholds.from_settings(settings)

    def customer_options(
        self,
        *,
        search: str = "",
        active_only: bool = False,
        selected_id: int | None = None,
        preserve_inactive_selected: bool = False,
        limit: int = 20,
    ) -> list[CustomerOption]:
        return self.repository.options(
            search=search,
            active_only=active_only,
            selected_id=selected_id,
            preserve_inactive_selected=preserve_inactive_selected,
            limit=limit,
        )

    @atomic_write
    def create(self, data: CustomerInput, user_id: int, *, force_duplicate: bool = False) -> Customer | PossibleDuplicate:
        if data.errors:
            raise CustomerValidationError(data)
        if data.document:
            existing = self.repository.by_document(data.document)
            if existing:
                raise DuplicateDocumentError(existing)
        possible = self.repository.possible_duplicates(data)
        if possible and not force_duplicate:
            return PossibleDuplicate(possible)
        try:
            customer = self.repository.create(data, user_id)
        except IntegrityError:
            self.session.rollback()
            existing = self.repository.by_document(data.document) if data.document else None
            if existing:
                raise DuplicateDocumentError(existing) from None
            raise
        self.repository.add_activity(customer, "CUSTOMER_CREATED", "Cliente cadastrado.", user_id)
        self.session.commit()
        return customer

    @atomic_write
    def update(self, customer_id: int, data: CustomerInput, user_id: int, *, force_duplicate: bool = False) -> Customer | PossibleDuplicate:
        customer = self.repository.get(customer_id)
        if customer is None:
            raise CustomerNotFoundError()
        if data.errors:
            raise CustomerValidationError(data)
        if data.document:
            existing = self.repository.by_document(data.document, exclude_id=customer_id)
            if existing:
                raise DuplicateDocumentError(existing)
        possible = self.repository.possible_duplicates(data, exclude_id=customer_id)
        if possible and not force_duplicate:
            return PossibleDuplicate(possible)
        changes = self._human_changes(customer, data)
        try:
            self.repository.update(customer, data, user_id)
        except IntegrityError:
            self.session.rollback()
            existing = self.repository.by_document(data.document, exclude_id=customer_id) if data.document else None
            if existing:
                raise DuplicateDocumentError(existing) from None
            raise
        description = "Cadastro atualizado" + (f": {', '.join(changes)}." if changes else ".")
        self.repository.add_activity(
            customer, "CUSTOMER_UPDATED", description, user_id,
            metadata_json=json.dumps({"changed_fields": changes}, ensure_ascii=False),
        )
        self.session.commit()
        return customer

    @staticmethod
    def _human_changes(customer: Customer, data: CustomerInput) -> list[str]:
        labels = {
            "type": "tipo", "birth_date": "data de nascimento",
            "name": "nome", "trade_name": "nome fantasia", "document": "documento",
            "phone": "telefone", "whatsapp": "WhatsApp", "email": "e-mail",
            "notes": "observações", "primary_contact": "contato principal",
        }
        changes = [label for field, label in labels.items() if getattr(customer, field) != getattr(data, field)]
        address = customer.address
        if any(getattr(address, field, None) != getattr(data, field) for field in (
            "cep", "street", "number", "complement", "neighborhood", "city", "state"
        )):
            changes.append("endereço")
        return changes

    @atomic_write
    def set_active(self, customer_id: int, active: bool, user_id: int) -> Customer:
        customer = self.repository.get(customer_id)
        if customer is None:
            raise CustomerNotFoundError()
        if customer.is_active == active:
            return customer
        customer.is_active = active
        customer.updated_by = user_id
        activity_type = "CUSTOMER_REACTIVATED" if active else "CUSTOMER_DEACTIVATED"
        description = "Cliente reativado." if active else "Cliente inativado."
        self.repository.add_activity(customer, activity_type, description, user_id)
        self.session.commit()
        return customer

    @atomic_write
    def register_visit(self, customer_id: int, occurred_at: datetime, note: str, user_id: int) -> None:
        customer = self.repository.get(customer_id)
        if customer is None:
            raise CustomerNotFoundError()
        if occurred_at.tzinfo is None:
            occurred_at = occurred_at.replace(tzinfo=timezone.utc)
        occurred_at = occurred_at.astimezone(timezone.utc)
        description = note.strip()[:300] or "Visita registrada sem observação."
        self.repository.add_activity(customer, "VISIT", description, user_id, occurred_at=occurred_at)
        self.session.commit()

    def profile(
        self,
        customer_id: int,
        *,
        now: datetime | None = None,
        page: int = 1,
        per_page: int = 25,
    ) -> CustomerProfile:
        customer = self.repository.get(customer_id)
        if customer is None:
            raise CustomerNotFoundError()
        last = self.repository.last_relevant_activity(customer_id)
        code, label, days = self.thresholds.classify(last, now=now)
        per_page = per_page if per_page in {10, 25, 50, 100} else 25
        activities, total, effective_page = self.activities.customer_history_page(
            customer_id,
            page=max(1, page),
            per_page=per_page,
        )
        pages = max(1, math.ceil(total / per_page))
        actors = self.repository.actor_names({activity.created_by for activity in activities})
        return CustomerProfile(
            customer=customer,
            last_activity=last,
            relationship_code=code,
            relationship_label=label,
            relationship_badge=RELATIONSHIP_BADGES[code],
            inactive_days=days,
            last_service=self.repository.last_service_summary(customer_id),
            activities=activities,
            actor_names=actors,
            activity_total=total,
            activity_page=effective_page,
            activity_per_page=per_page,
            activity_pages=pages,
        )

    def list(
        self, *, search: str = "", customer_type: str = "ALL", active: str = "ALL",
        relationship: str = "ALL", inactive_days: int | None = None, sort: str = "name",
        page: int = 1, per_page: int = 25, now: datetime | None = None,
        month_start: datetime | None = None,
    ) -> CustomerListResult:
        current = now or datetime.now(timezone.utc)
        page = max(1, page)
        per_page = per_page if per_page in {25, 50, 100} else 25
        raw, total = self.repository.list_customers(
            search=search, customer_type=customer_type, active=active, relationship=relationship,
            inactive_days=inactive_days, sort=sort, page=page, per_page=per_page, now=current,
            thresholds=self.thresholds,
        )
        rows = [self._row(item, current) for item in raw]
        indicator_month_start = month_start or current.replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )
        indicators = self.repository.indicators(
            month_start=indicator_month_start,
            now=current,
            thresholds=self.thresholds,
        )
        pages = max(1, math.ceil(total / per_page))
        return CustomerListResult(
            rows, total, min(page, pages), per_page, pages, indicators, self.thresholds
        )

    def _row(self, item: CustomerListItem, now: datetime) -> CustomerRow:
        code, label, days = self.thresholds.classify(item.last_activity, now=now)
        return CustomerRow(item.customer, item.last_activity, code, label, RELATIONSHIP_BADGES[code], days)

    def general_history(self, **filters) -> list[ActivityItem]:
        return self.activities.general_history(**filters)

    def general_history_page(
        self,
        *,
        page: int = 1,
        per_page: int = 25,
        **filters,
    ) -> CustomerHistoryResult:
        per_page = per_page if per_page in {10, 25, 50, 100} else 25
        rows, total, effective_page = self.activities.general_history_page(
            page=max(1, page),
            per_page=per_page,
            **filters,
        )
        return CustomerHistoryResult(
            rows=rows,
            total=total,
            page=effective_page,
            per_page=per_page,
            pages=max(1, math.ceil(total / per_page)),
        )

    def relationship_counts(self, *, now: datetime | None = None) -> dict[str, int]:
        return self.repository.relationship_counts(
            now=now or datetime.now(timezone.utc),
            thresholds=self.thresholds,
        )

    @staticmethod
    def activity_label(activity_type: str) -> str:
        return ACTIVITY_LABELS.get(activity_type, "Atividade registrada")

    @staticmethod
    def form_from_customer(customer: Customer) -> dict[str, str]:
        address = customer.address
        data = CustomerInput(
            type=customer.type, name=customer.name, trade_name=customer.trade_name,
            document=customer.document, birth_date=customer.birth_date,
            primary_contact=customer.primary_contact, phone=customer.phone,
            whatsapp=customer.whatsapp, email=customer.email, notes=customer.notes,
            is_active=customer.is_active,
            cep=address.cep if address else None, street=address.street if address else None,
            number=address.number if address else None, complement=address.complement if address else None,
            neighborhood=address.neighborhood if address else None, city=address.city if address else None,
            state=address.state if address else None,
        )
        return data.as_form()
