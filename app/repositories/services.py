from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.models import Service, ServiceCategory, ServicePrice, User


@dataclass(frozen=True, slots=True)
class ServiceListItem:
    service: Service
    current_price: Decimal
    price_changed_at: datetime


@dataclass(frozen=True, slots=True)
class CategoryListItem:
    category: ServiceCategory
    service_count: int


class ServiceCategoryRepository:
    def __init__(self, session: Session):
        self.session = session

    def get(self, category_id: int) -> ServiceCategory | None:
        if not 0 < category_id < 2**63:
            return None
        return self.session.get(ServiceCategory, category_id)

    def by_name(self, name: str, exclude_id: int | None = None) -> ServiceCategory | None:
        query = select(ServiceCategory).where(func.casefold(ServiceCategory.name) == name.casefold())
        if exclude_id:
            query = query.where(ServiceCategory.id != exclude_id)
        return self.session.scalar(query)

    def all(self, *, active_only: bool = False) -> list[ServiceCategory]:
        query = select(ServiceCategory).order_by(ServiceCategory.sort_order, ServiceCategory.name)
        if active_only:
            query = query.where(ServiceCategory.is_active.is_(True))
        return list(self.session.scalars(query))

    def list_with_counts(self) -> list[CategoryListItem]:
        rows = self.session.execute(
            select(ServiceCategory, func.count(Service.id)).outerjoin(Service).group_by(ServiceCategory.id)
            .order_by(ServiceCategory.sort_order, ServiceCategory.name)
        ).all()
        return [CategoryListItem(row[0], int(row[1])) for row in rows]


class ServiceRepository:
    def __init__(self, session: Session):
        self.session = session

    def get(self, service_id: int) -> Service | None:
        if not 0 < service_id < 2**63:
            return None
        return self.session.scalar(
            select(Service).where(Service.id == service_id).options(selectinload(Service.category), selectinload(Service.prices))
        )

    def by_code(self, code: str, exclude_id: int | None = None) -> Service | None:
        query = select(Service).where(func.casefold(Service.code) == code.casefold())
        if exclude_id:
            query = query.where(Service.id != exclude_id)
        return self.session.scalar(query)

    def name_candidates(self, name: str, exclude_id: int | None = None) -> list[Service]:
        query = select(Service).options(selectinload(Service.category))
        if exclude_id:
            query = query.where(Service.id != exclude_id)
        return list(self.session.scalars(query))

    def list(
        self, *, search: str = "", category: str = "ALL", billing_unit: str = "ALL",
        active: str = "ACTIVE", sort: str = "name", page: int = 1, per_page: int = 25,
    ) -> tuple[list[ServiceListItem], int]:
        current_amount = select(ServicePrice.amount).where(ServicePrice.service_id == Service.id, ServicePrice.valid_to.is_(None)).correlate(Service).scalar_subquery()
        current_since = select(ServicePrice.valid_from).where(ServicePrice.service_id == Service.id, ServicePrice.valid_to.is_(None)).correlate(Service).scalar_subquery()
        query = select(Service, current_amount.label("price"), current_since.label("price_since")).options(selectinload(Service.category))
        term = search.strip().casefold()
        if term:
            escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            like = f"%{escaped}%"
            query = query.outerjoin(ServiceCategory).where(or_(
                func.casefold(Service.name).like(like, escape="\\"), func.casefold(func.coalesce(Service.code, "")).like(like, escape="\\"),
                func.casefold(func.coalesce(Service.description, "")).like(like, escape="\\"), func.casefold(func.coalesce(ServiceCategory.name, "")).like(like, escape="\\"),
            ))
        if category == "NONE":
            query = query.where(Service.category_id.is_(None))
        elif category.isascii() and category.isdecimal() and len(category) < 19:
            query = query.where(Service.category_id == int(category))
        if billing_unit != "ALL":
            query = query.where(Service.billing_unit == billing_unit)
        if active in {"ACTIVE", "INACTIVE"}:
            query = query.where(Service.is_active.is_(active == "ACTIVE"))
        total = int(self.session.scalar(select(func.count()).select_from(query.order_by(None).subquery())) or 0)
        ordering = {
            "name": (Service.name.asc(),), "name_desc": (Service.name.desc(),),
            "price_asc": (current_amount.asc(), Service.name.asc()), "price_desc": (current_amount.desc(), Service.name.asc()),
            "newest": (Service.created_at.desc(),), "oldest": (Service.created_at.asc(),),
        }.get(sort, (Service.name.asc(),))
        page = min(max(1, page), max(1, (total + per_page - 1) // per_page))
        rows = self.session.execute(query.order_by(*ordering, Service.id).offset((page - 1) * per_page).limit(per_page)).all()
        return [ServiceListItem(row[0], row[1], row[2]) for row in rows], total

    def indicators(self) -> dict[str, int]:
        return {
            "total": int(self.session.scalar(select(func.count(Service.id))) or 0),
            "active": int(self.session.scalar(select(func.count(Service.id)).where(Service.is_active.is_(True))) or 0),
            "inactive": int(self.session.scalar(select(func.count(Service.id)).where(Service.is_active.is_(False))) or 0),
            "categories": int(self.session.scalar(select(func.count(ServiceCategory.id)).where(ServiceCategory.is_active.is_(True))) or 0),
        }


class ServicePriceRepository:
    def __init__(self, session: Session):
        self.session = session

    def current(self, service_id: int) -> ServicePrice | None:
        return self.session.scalar(select(ServicePrice).where(ServicePrice.service_id == service_id, ServicePrice.valid_to.is_(None)))

    def history(self, service_id: int) -> list[ServicePrice]:
        return list(self.session.scalars(select(ServicePrice).where(ServicePrice.service_id == service_id).order_by(ServicePrice.valid_from.desc())))

    def actor_names(self, prices: list[ServicePrice]) -> dict[int, str]:
        ids = {price.created_by for price in prices}
        if not ids:
            return {}
        return {user.id: user.display_name for user in self.session.scalars(select(User).where(User.id.in_(ids)))}
