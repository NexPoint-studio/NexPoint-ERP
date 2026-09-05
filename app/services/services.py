from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from difflib import SequenceMatcher
import json
import math

from sqlalchemy.orm import Session
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from app.services.transactions import atomic_write

from app.models import AuditEvent, Service, ServiceCategory, ServicePrice
from app.repositories import ServiceCategoryRepository, ServicePriceRepository, ServiceRepository
from app.repositories.services import CategoryListItem, ServiceListItem
from app.services.service_validation import CategoryInput, ServiceInput, parse_money


class ServiceValidationError(Exception):
    def __init__(self, data):
        self.data = data


class ServiceNotFoundError(Exception):
    pass


class CategoryNotFoundError(Exception):
    pass


class DuplicateCodeError(Exception):
    pass


class DuplicateCategoryError(Exception):
    pass


class PriceConflictError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PossibleServiceDuplicate:
    services: list[Service]


@dataclass(frozen=True, slots=True)
class ServiceListResult:
    rows: list[ServiceListItem]
    total: int
    page: int
    per_page: int
    pages: int
    indicators: dict[str, int]


@dataclass(frozen=True, slots=True)
class ServiceProfile:
    service: Service
    current_price: ServicePrice
    actor_names: dict[int, str]


class CatalogService:
    def __init__(self, session: Session):
        self.session = session
        self.repository = ServiceRepository(session)
        self.categories = ServiceCategoryRepository(session)
        self.prices = ServicePriceRepository(session)

    def _audit(self, user_id: int, action: str, resource: str, details: dict | None = None) -> None:
        self.session.add(AuditEvent(
            user_id=user_id, action=action, resource=resource,
            details=json.dumps(details, ensure_ascii=False, default=str) if details else None,
        ))

    def _validate(self, data: ServiceInput, *, exclude_id: int | None = None) -> None:
        if data.category_id is not None and self.categories.get(data.category_id) is None:
            data.errors["category_id"] = "Categoria não encontrada."
        if data.errors:
            raise ServiceValidationError(data)
        if data.code and self.repository.by_code(data.code, exclude_id):
            raise DuplicateCodeError()

    def _duplicates(self, data: ServiceInput, *, exclude_id: int | None = None) -> list[Service]:
        matches = []
        wanted = data.name.casefold()
        for service in self.repository.name_candidates(data.name, exclude_id):
            ratio = SequenceMatcher(None, wanted, service.name.casefold()).ratio()
            if service.name.casefold() == wanted or ratio >= 0.86:
                matches.append(service)
        return matches

    @atomic_write
    def create(self, data: ServiceInput, user_id: int, *, force_duplicate: bool = False) -> Service | PossibleServiceDuplicate:
        self._validate(data)
        duplicates = self._duplicates(data)
        if duplicates and not force_duplicate:
            return PossibleServiceDuplicate(duplicates)
        service = Service(
            code=data.code, name=data.name, description=data.description, category_id=data.category_id,
            billing_unit=data.billing_unit, is_active=data.is_active, created_by=user_id, updated_by=user_id,
        )
        self.session.add(service)
        try:
            self.session.flush()
        except IntegrityError:
            self.session.rollback()
            if data.code and self.repository.by_code(data.code):
                raise DuplicateCodeError() from None
            raise
        self.session.add(ServicePrice(service_id=service.id, amount=data.initial_price, created_by=user_id))
        self._audit(user_id, "service.created", f"services/{service.id}", {"name": service.name})
        self.session.commit()
        return service

    @atomic_write
    def update(self, service_id: int, data: ServiceInput, user_id: int, *, force_duplicate: bool = False) -> Service | PossibleServiceDuplicate:
        service = self.repository.get(service_id)
        if service is None:
            raise ServiceNotFoundError()
        self._validate(data, exclude_id=service_id)
        duplicates = self._duplicates(data, exclude_id=service_id)
        if duplicates and not force_duplicate:
            return PossibleServiceDuplicate(duplicates)
        changes = {}
        for field in ("code", "name", "description", "category_id", "billing_unit", "is_active"):
            before, after = getattr(service, field), getattr(data, field)
            if before != after:
                changes[field] = {"from": before, "to": after}
                setattr(service, field, after)
        service.updated_by = user_id
        self._audit(user_id, "service.updated", f"services/{service.id}", changes)
        self.session.commit()
        return service

    @atomic_write
    def set_active(self, service_id: int, active: bool, user_id: int) -> Service:
        service = self.repository.get(service_id)
        if service is None:
            raise ServiceNotFoundError()
        if service.is_active != active:
            service.is_active = active
            service.updated_by = user_id
            action = "service.reactivated" if active else "service.deactivated"
            self._audit(user_id, action, f"services/{service.id}")
            self.session.commit()
        return service

    @atomic_write
    def change_price(self, service_id: int, raw_amount: object, reason: str, user_id: int, *, at: datetime | None = None) -> ServicePrice:
        service = self.repository.get(service_id)
        if service is None:
            raise ServiceNotFoundError()
        amount = parse_money(raw_amount)
        current = self.prices.current(service_id)
        if current is None:
            raise ValueError("O serviço não possui preço vigente.")
        moment = at or datetime.now(timezone.utc)
        current_from = current.valid_from
        if moment.tzinfo is not None:
            moment = moment.astimezone(timezone.utc).replace(tzinfo=None)
        if current_from.tzinfo is not None:
            current_from = current_from.astimezone(timezone.utc).replace(tzinfo=None)
        if moment <= current_from:
            moment = current_from + timedelta(microseconds=1)
        closed = self.session.execute(update(ServicePrice).where(ServicePrice.id == current.id, ServicePrice.valid_to.is_(None)).values(valid_to=moment).execution_options(synchronize_session=False))
        if closed.rowcount != 1:
            raise PriceConflictError("O preço foi alterado em outra solicitação. Confira o valor atual e tente novamente.")
        self.session.expire(current, ["valid_to"])
        new_price = ServicePrice(
            service_id=service_id, amount=amount, valid_from=moment,
            reason=reason.strip()[:300] or None, created_by=user_id,
        )
        self.session.add(new_price)
        service.updated_by = user_id
        self._audit(user_id, "service.price_changed", f"services/{service.id}", {"from": current.amount, "to": amount, "reason": new_price.reason})
        self.session.commit()
        return new_price

    def list(self, **filters) -> ServiceListResult:
        page = max(1, int(filters.pop("page", 1)))
        per_page = 25
        rows, total = self.repository.list(page=page, per_page=per_page, **filters)
        pages = max(1, math.ceil(total / per_page))
        return ServiceListResult(rows, total, min(page, pages), per_page, pages, self.repository.indicators())

    def profile(self, service_id: int) -> ServiceProfile:
        service = self.repository.get(service_id)
        if service is None:
            raise ServiceNotFoundError()
        current = self.prices.current(service_id)
        if current is None:
            raise ServiceNotFoundError()
        return ServiceProfile(service, current, self.prices.actor_names(service.prices))

    @atomic_write
    def category_create(self, data: CategoryInput, user_id: int) -> ServiceCategory:
        if data.errors:
            raise ServiceValidationError(data)
        self.session.execute(update(ServiceCategory).where(ServiceCategory.id == -1).values(sort_order=0))
        if self.categories.by_name(data.name):
            raise DuplicateCategoryError()
        category = ServiceCategory(
            name=data.name, description=data.description, sort_order=data.sort_order, is_active=data.is_active,
            created_by=user_id, updated_by=user_id,
        )
        self.session.add(category)
        self.session.flush()
        self._audit(user_id, "service_category.created", f"service_categories/{category.id}", {"name": category.name})
        self.session.commit()
        return category

    @atomic_write
    def category_update(self, category_id: int, data: CategoryInput, user_id: int) -> ServiceCategory:
        self.session.execute(update(ServiceCategory).where(ServiceCategory.id == -1).values(sort_order=0))
        category = self.categories.get(category_id)
        if category is None:
            raise CategoryNotFoundError()
        if data.errors:
            raise ServiceValidationError(data)
        if self.categories.by_name(data.name, category_id):
            raise DuplicateCategoryError()
        changes = {}
        for field in ("name", "description", "sort_order", "is_active"):
            before, after = getattr(category, field), getattr(data, field)
            if before != after:
                changes[field] = {"from": before, "to": after}
                setattr(category, field, after)
        category.updated_by = user_id
        self._audit(user_id, "service_category.updated", f"service_categories/{category.id}", changes)
        self.session.commit()
        return category

    @atomic_write
    def category_set_active(self, category_id: int, active: bool, user_id: int) -> ServiceCategory:
        category = self.categories.get(category_id)
        if category is None:
            raise CategoryNotFoundError()
        if category.is_active != active:
            category.is_active = active
            category.updated_by = user_id
            action = "service_category.reactivated" if active else "service_category.deactivated"
            self._audit(user_id, action, f"service_categories/{category.id}")
            self.session.commit()
        return category
