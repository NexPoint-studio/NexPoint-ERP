"""Consultas persistentes do módulo de Notas de Serviço."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.core.money import cents_to_decimal, decimal_to_cents
from app.models import BillingUnit, Customer, Service, ServiceNote, ServicePrice, ServiceCategoryLink, User


@dataclass(frozen=True, slots=True)
class AvailableService:
    id: int
    name: str
    code: str | None
    description: str | None
    category_name: str | None
    current_price: Decimal
    billing_unit: BillingUnit
    category_id: int | None = None

    @property
    def current_price_cents(self) -> int:
        return decimal_to_cents(self.current_price)


class NoteRepository:
    def __init__(self, session: Session):
        self.session = session

    @staticmethod
    def _valid_id(value: int) -> bool:
        return isinstance(value, int) and not isinstance(value, bool) and 0 < value < 2**63

    def get(self, note_id: int) -> ServiceNote | None:
        if not self._valid_id(note_id):
            return None
        query = (
            select(ServiceNote)
            .where(ServiceNote.id == note_id)
            .options(
                selectinload(ServiceNote.customer),
                selectinload(ServiceNote.items),
                selectinload(ServiceNote.events),
            )
            .execution_options(populate_existing=True)
        )
        return self.session.scalar(query)

    def by_number(self, number_normalized: str, series_normalized: str, *, exclude_id: int | None = None) -> ServiceNote | None:
        query = select(ServiceNote).where(
            ServiceNote.number_normalized == number_normalized,
            ServiceNote.series_normalized == series_normalized,
        )
        if exclude_id is not None:
            query = query.where(ServiceNote.id != exclude_id)
        return self.session.scalar(query.limit(1))

    def customer(self, customer_id: int) -> Customer | None:
        if not self._valid_id(customer_id):
            return None
        return self.session.get(Customer, customer_id)

    def customers(self, *, active_only: bool = False) -> list[Customer]:
        query = select(Customer).order_by(Customer.name, Customer.id)
        if active_only:
            query = query.where(Customer.is_active.is_(True))
        return list(self.session.scalars(query))

    def _catalog_rows(self, service_id: int | None = None, *, active_only: bool = True):
        query = (
            select(Service, ServicePrice.amount)
            .join(BillingUnit, Service.billing_unit_id == BillingUnit.id)
            .outerjoin(
                ServicePrice,
                and_(ServicePrice.service_id == Service.id, ServicePrice.valid_to.is_(None)),
            )
            .options(
                selectinload(Service.category), selectinload(Service.billing_unit),
                selectinload(Service.category_links).selectinload(ServiceCategoryLink.category),
            )
            .order_by(Service.name, Service.id)
        )
        if service_id is not None:
            query = query.where(Service.id == service_id)
        if active_only:
            query = query.where(Service.is_active.is_(True), BillingUnit.is_active.is_(True))
        return self.session.execute(query).all()

    @staticmethod
    def _combinations(service: Service, default_price: Decimal | None, *, active_only: bool):
        if service.category_links:
            candidates = [(link.category, link.price_cents) for link in service.category_links]
        else:
            # Compatibility with uncategorized/legacy rows; migrated categorized
            # services use category_links as the authoritative association set.
            candidates = [(service.category, None)]
        for category, override_cents in candidates:
            if active_only and category is not None and not category.is_active:
                continue
            price = cents_to_decimal(override_cents) if override_cents is not None else default_price
            if price is None:
                continue
            yield AvailableService(
                id=service.id, name=service.name, code=service.code,
                description=service.description,
                category_id=category.id if category else None,
                category_name=category.name if category else None,
                current_price=price, billing_unit=service.billing_unit,
            )

    def available_service(self, service_id: int, *, category_id: int | None = None,
                          active_only: bool = True) -> AvailableService | None:
        if not self._valid_id(service_id) or (category_id is not None and not self._valid_id(category_id)):
            return None
        rows = self._catalog_rows(service_id, active_only=active_only)
        if not rows:
            return None
        service, default_price = rows[0]
        # Never infer a category from availability: a disabled or unpriced
        # association still makes a multi-category service ambiguous.
        if category_id is None and len(service.category_links) > 1:
            return None
        choices = list(self._combinations(service, default_price, active_only=active_only))
        if category_id is not None:
            choices = [choice for choice in choices if choice.category_id == category_id]
        return choices[0] if len(choices) == 1 else None

    def available_services(self) -> list[AvailableService]:
        return [
            choice
            for service, price in self._catalog_rows()
            for choice in self._combinations(service, price, active_only=True)
        ]

    def list(
        self,
        *,
        search: str = "",
        customer_id: int | None = None,
        operational_status: str = "ALL",
        financial_status: str = "ALL",
        deadline_status: str = "ALL",
        received_from: datetime | None = None,
        received_to: datetime | None = None,
        today_start: datetime | None = None,
        tomorrow_start: datetime | None = None,
        page: int = 1,
        per_page: int = 25,
    ) -> tuple[list[ServiceNote], int]:
        conditions = []
        wanted = search.strip()[:120]
        if wanted:
            escaped = wanted.casefold().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            folded = f"%{escaped}%"
            conditions.append(or_(
                func.casefold(ServiceNote.number_original).like(folded, escape="\\"),
                func.casefold(func.coalesce(ServiceNote.series_original, "")).like(folded, escape="\\"),
                func.casefold(Customer.name).like(folded, escape="\\"),
                func.casefold(func.coalesce(ServiceNote.notes, "")).like(folded, escape="\\"),
            ))
        if customer_id is not None:
            conditions.append(ServiceNote.customer_id == customer_id)
        if operational_status != "ALL":
            conditions.append(ServiceNote.operational_status == operational_status)
        if financial_status == "SALDO_DEVEDOR":
            conditions.extend((
                ServiceNote.operational_status == "FECHADO",
                ServiceNote.financial_status != "PAGO",
            ))
        elif financial_status != "ALL":
            conditions.append(ServiceNote.financial_status == financial_status)
        if received_from is not None:
            conditions.append(ServiceNote.received_at >= received_from)
        if received_to is not None:
            conditions.append(ServiceNote.received_at < received_to)

        if deadline_status == "OVERDUE":
            conditions.extend((
                ServiceNote.operational_status.in_(("RECEBIDO", "EM_ANDAMENTO")),
                ServiceNote.ready_at.is_(None),
                ServiceNote.expected_ready_at < today_start,
            ))
        elif deadline_status == "TODAY":
            conditions.extend((
                ServiceNote.operational_status.in_(("RECEBIDO", "EM_ANDAMENTO")),
                ServiceNote.ready_at.is_(None),
                ServiceNote.expected_ready_at >= today_start,
                ServiceNote.expected_ready_at < tomorrow_start,
            ))

        base = select(ServiceNote).join(Customer, Customer.id == ServiceNote.customer_id)
        count_query = select(func.count(ServiceNote.id)).join(Customer, Customer.id == ServiceNote.customer_id)
        if conditions:
            base = base.where(*conditions)
            count_query = count_query.where(*conditions)
        total = int(self.session.scalar(count_query) or 0)
        query = (
            base.options(selectinload(ServiceNote.customer))
            .order_by(ServiceNote.received_at.desc(), ServiceNote.id.desc())
            .offset((page - 1) * per_page)
            .limit(per_page)
        )
        return list(self.session.scalars(query)), total

    def actor_names(self, note: ServiceNote) -> dict[int, str]:
        ids = {note.created_by, note.updated_by}
        if note.canceled_by is not None:
            ids.add(note.canceled_by)
        ids.update(event.created_by for event in note.events)
        if not ids:
            return {}
        return {
            user.id: user.display_name
            for user in self.session.scalars(select(User).where(User.id.in_(ids)))
        }
