from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from difflib import SequenceMatcher

from sqlalchemy import Select, and_, case, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.models import Customer, CustomerActivity, CustomerAddress, User
from app.services.customer_validation import CustomerInput, digits


RELEVANT_ACTIVITY_TYPES = ("VISIT", "SERVICE_COMPLETED")


@dataclass(frozen=True, slots=True)
class CustomerListItem:
    customer: Customer
    last_activity: datetime | None


@dataclass(frozen=True, slots=True)
class ActivityItem:
    activity: CustomerActivity
    customer: Customer
    actor_name: str


class CustomerRepository:
    def __init__(self, session: Session):
        self.session = session

    @staticmethod
    def last_relevant_activity_subquery():
        return (
            select(func.max(CustomerActivity.occurred_at))
            .where(
                CustomerActivity.customer_id == Customer.id,
                CustomerActivity.activity_type.in_(RELEVANT_ACTIVITY_TYPES),
            )
            .correlate(Customer)
            .scalar_subquery()
        )

    def get(self, customer_id: int) -> Customer | None:
        if not 0 < customer_id < 2**63:
            return None
        return self.session.scalar(
            select(Customer)
            .where(Customer.id == customer_id)
            .options(selectinload(Customer.address), selectinload(Customer.activities))
        )

    def actor_names(self, user_ids: set[int]) -> dict[int, str]:
        if not user_ids:
            return {}
        return {row.id: row.display_name for row in self.session.scalars(select(User).where(User.id.in_(user_ids)))}

    def by_document(self, document: str, *, exclude_id: int | None = None) -> Customer | None:
        query = select(Customer).where(Customer.document == document)
        if exclude_id is not None:
            query = query.where(Customer.id != exclude_id)
        return self.session.scalar(query)

    def possible_duplicates(self, data: CustomerInput, *, exclude_id: int | None = None) -> list[Customer]:
        criteria = []
        if data.phone:
            criteria.append(Customer.phone == data.phone)
        if data.whatsapp:
            criteria.append(Customer.whatsapp == data.whatsapp)
        if not criteria:
            return []
        query = select(Customer).where(or_(*criteria)).order_by(Customer.name)
        if exclude_id is not None:
            query = query.where(Customer.id != exclude_id)
        candidates = list(self.session.scalars(query))
        # Telefone/WhatsApp igual já é aviso; a semelhança deixa a mensagem mais útil.
        return sorted(
            candidates,
            key=lambda customer: SequenceMatcher(None, data.name.casefold(), customer.name.casefold()).ratio(),
            reverse=True,
        )

    def create(self, data: CustomerInput, user_id: int) -> Customer:
        customer = Customer(
            type=data.type, name=data.name, trade_name=data.trade_name, document=data.document,
            birth_date=data.birth_date, primary_contact=data.primary_contact, phone=data.phone,
            whatsapp=data.whatsapp, email=data.email, notes=data.notes, is_active=data.is_active,
            created_by=user_id, updated_by=user_id,
        )
        customer.address = CustomerAddress(
            cep=data.cep, street=data.street, number=data.number, complement=data.complement,
            neighborhood=data.neighborhood, city=data.city, state=data.state,
        )
        self.session.add(customer)
        self.session.flush()
        return customer

    def update(self, customer: Customer, data: CustomerInput, user_id: int) -> None:
        for field in (
            "type", "name", "trade_name", "document", "birth_date", "primary_contact",
            "phone", "whatsapp", "email", "notes", "is_active",
        ):
            setattr(customer, field, getattr(data, field))
        customer.updated_by = user_id
        if customer.address is None:
            customer.address = CustomerAddress()
        for field in ("cep", "street", "number", "complement", "neighborhood", "city", "state"):
            setattr(customer.address, field, getattr(data, field))
        self.session.flush()

    def add_activity(
        self, customer: Customer, activity_type: str, description: str, user_id: int,
        *, occurred_at: datetime | None = None, metadata_json: str | None = None,
    ) -> CustomerActivity:
        activity = CustomerActivity(
            customer_id=customer.id, activity_type=activity_type, description=description,
            created_by=user_id, metadata_json=metadata_json,
        )
        if occurred_at is not None:
            activity.occurred_at = occurred_at
        self.session.add(activity)
        self.session.flush()
        return activity

    def list_customers(
        self, *, search: str = "", customer_type: str = "ALL", active: str = "ALL",
        relationship: str = "ALL", inactive_days: int | None = None,
        sort: str = "name", page: int = 1, per_page: int = 25, now: datetime,
    ) -> tuple[list[CustomerListItem], int]:
        last_activity = self.last_relevant_activity_subquery()
        query: Select = select(Customer, last_activity.label("last_activity")).options(selectinload(Customer.address))
        term = search.strip()
        if term:
            escaped = term.casefold().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            like = f"%{escaped}%"
            number = digits(term)
            fields = [
                func.casefold(Customer.name).like(like, escape="\\"), func.casefold(func.coalesce(Customer.trade_name, "")).like(like, escape="\\"),
                func.casefold(func.coalesce(Customer.email, "")).like(like, escape="\\"),
            ]
            if number:
                fields.extend([
                    func.coalesce(Customer.phone, "").like(f"%{number}%"),
                    func.coalesce(Customer.whatsapp, "").like(f"%{number}%"),
                    func.coalesce(Customer.document, "").like(f"%{number}%"),
                ])
            query = query.where(or_(*fields))
        if customer_type in {"PERSON", "COMPANY"}:
            query = query.where(Customer.type == customer_type)
        if active in {"ACTIVE", "INACTIVE"}:
            query = query.where(Customer.is_active.is_(active == "ACTIVE"))

        thresholds = {"30_PLUS": 30, "60_PLUS": 60, "90_PLUS": 90}
        if relationship == "NEVER":
            query = query.where(last_activity.is_(None))
        elif relationship == "RECENT":
            query = query.where(last_activity >= now - timedelta(days=30))
        elif relationship in thresholds:
            days = thresholds[relationship]
            query = query.where(last_activity.is_not(None), last_activity <= now - timedelta(days=days))
        if inactive_days is not None:
            query = query.where(last_activity.is_not(None), last_activity <= now - timedelta(days=min(max(0, inactive_days), 365000)))

        count_query = select(func.count()).select_from(query.order_by(None).subquery())
        total = int(self.session.scalar(count_query) or 0)
        ordering = {
            "name": (Customer.name.asc(),),
            "newest": (Customer.created_at.desc(),),
            "oldest": (Customer.created_at.asc(),),
            "last_recent": (case((last_activity.is_(None), 1), else_=0), last_activity.desc()),
            "last_oldest": (case((last_activity.is_(None), 1), else_=0), last_activity.asc()),
            "most_inactive": (case((last_activity.is_(None), 0), else_=1), last_activity.asc()),
        }.get(sort, (Customer.name.asc(),))
        rows = self.session.execute(
            query.order_by(*ordering, Customer.id).offset((min(max(1, page), max(1, (total + per_page - 1) // per_page)) - 1) * per_page).limit(per_page)
        ).all()
        return [CustomerListItem(customer=row[0], last_activity=row[1]) for row in rows], total

    def indicators(self, *, month_start: datetime, stale_before: datetime) -> dict[str, int]:
        last_activity = self.last_relevant_activity_subquery()
        return {
            "total": int(self.session.scalar(select(func.count(Customer.id))) or 0),
            "active": int(self.session.scalar(select(func.count(Customer.id)).where(Customer.is_active.is_(True))) or 0),
            "new_month": int(self.session.scalar(select(func.count(Customer.id)).where(Customer.created_at >= month_start)) or 0),
            "long_inactive": int(self.session.scalar(
                select(func.count(Customer.id)).where(last_activity.is_not(None), last_activity < stale_before)
            ) or 0),
        }

    def last_relevant_activity(self, customer_id: int) -> datetime | None:
        return self.session.scalar(
            select(func.max(CustomerActivity.occurred_at)).where(
                CustomerActivity.customer_id == customer_id,
                CustomerActivity.activity_type.in_(RELEVANT_ACTIVITY_TYPES),
            )
        )


class CustomerActivityRepository:
    def __init__(self, session: Session):
        self.session = session

    def general_history(
        self, *, search: str = "", customer_id: int | None = None, activity_type: str = "ALL",
        user_id: int | None = None, date_from: datetime | None = None, date_to: datetime | None = None,
        limit: int = 200,
    ) -> list[ActivityItem]:
        query = (
            select(CustomerActivity, Customer, User.display_name)
            .join(Customer, Customer.id == CustomerActivity.customer_id)
            .join(User, User.id == CustomerActivity.created_by)
        )
        if search.strip():
            escaped = search.strip().casefold().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            like = f"%{escaped}%"
            query = query.where(or_(func.casefold(Customer.name).like(like, escape="\\"), func.casefold(CustomerActivity.description).like(like, escape="\\")))
        if customer_id:
            query = query.where(Customer.id == customer_id)
        if activity_type != "ALL":
            query = query.where(CustomerActivity.activity_type == activity_type)
        if user_id:
            query = query.where(User.id == user_id)
        if date_from:
            query = query.where(CustomerActivity.occurred_at >= date_from)
        if date_to:
            query = query.where(CustomerActivity.occurred_at <= date_to)
        rows = self.session.execute(query.order_by(CustomerActivity.occurred_at.desc()).limit(limit)).all()
        return [ActivityItem(activity=row[0], customer=row[1], actor_name=row[2]) for row in rows]

    def customers_for_filter(self) -> list[Customer]:
        return list(self.session.scalars(select(Customer).order_by(Customer.name)))

    def users_for_filter(self) -> list[User]:
        return list(self.session.scalars(select(User).order_by(User.display_name)))
