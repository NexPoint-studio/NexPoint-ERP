from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from difflib import SequenceMatcher

from sqlalchemy import Select, case, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.core.customer_config import InactivityThresholds
from app.models import Customer, CustomerActivity, CustomerAddress, ServiceNote, User
from app.services.customer_validation import CustomerInput, digits


RELEVANT_ACTIVITY_TYPES = ("VISIT", "SERVICE_CREATED")


@dataclass(frozen=True, slots=True)
class CustomerListItem:
    customer: Customer
    last_activity: datetime | None


@dataclass(frozen=True, slots=True)
class CustomerOption:
    id: int
    name: str
    is_active: bool


@dataclass(frozen=True, slots=True)
class ActivityItem:
    activity: CustomerActivity
    customer: Customer
    actor_name: str


@dataclass(frozen=True, slots=True)
class LastServiceSummary:
    note_id: int
    number: str
    series: str | None
    received_at: datetime
    operational_status: str
    service_names: tuple[str, ...]


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
            .options(selectinload(Customer.address))
        )

    def options(
        self,
        *,
        search: str = "",
        active_only: bool = False,
        selected_id: int | None = None,
        preserve_inactive_selected: bool = False,
        limit: int = 20,
    ) -> list[CustomerOption]:
        """Retorna uma projeção pequena para seletores com busca incremental."""
        safe_limit = min(max(int(limit), 1), 50)
        selected = None
        if selected_id is not None and 0 < selected_id < 2**63:
            selected_query = select(
                Customer.id, Customer.name, Customer.is_active
            ).where(Customer.id == selected_id)
            if active_only and not preserve_inactive_selected:
                selected_query = selected_query.where(Customer.is_active.is_(True))
            selected = self.session.execute(selected_query).one_or_none()

        query = select(Customer.id, Customer.name, Customer.is_active)
        if active_only:
            query = query.where(Customer.is_active.is_(True))
        term = search.strip()[:120]
        if term:
            escaped = (
                term.casefold()
                .replace("\\", "\\\\")
                .replace("%", "\\%")
                .replace("_", "\\_")
            )
            like = f"%{escaped}%"
            number = digits(term)
            fields = [
                func.casefold(Customer.name).like(like, escape="\\"),
                func.casefold(func.coalesce(Customer.trade_name, "")).like(
                    like, escape="\\"
                ),
                func.casefold(func.coalesce(Customer.email, "")).like(
                    like, escape="\\"
                ),
            ]
            if number:
                fields.extend([
                    func.coalesce(Customer.phone, "").like(f"%{number}%"),
                    func.coalesce(Customer.whatsapp, "").like(f"%{number}%"),
                    func.coalesce(Customer.document, "").like(f"%{number}%"),
                ])
            query = query.where(or_(*fields))
        rows = self.session.execute(
            query.order_by(func.casefold(Customer.name), Customer.id).limit(safe_limit)
        ).all()

        result: list[CustomerOption] = []
        seen: set[int] = set()
        for row in ([selected] if selected is not None else []) + rows:
            if row.id in seen:
                continue
            result.append(CustomerOption(int(row.id), str(row.name), bool(row.is_active)))
            seen.add(int(row.id))
            if len(result) == safe_limit:
                break
        return result

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
        # A edição cadastral preserva o status, mesmo se is_active vier no payload.
        for field in (
            "type", "name", "trade_name", "document", "birth_date", "primary_contact",
            "phone", "whatsapp", "email", "notes",
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
        source_type: str | None = None, source_id: str | None = None,
        source_reference: str | None = None,
    ) -> CustomerActivity:
        if (source_type is None) != (source_id is None):
            raise ValueError("A origem da atividade exige tipo e identificador.")
        normalized_source_type = source_type.strip().upper() if source_type is not None else None
        normalized_source_id = source_id.strip() if source_id is not None else None
        if source_type is not None and (not normalized_source_type or not normalized_source_id):
            raise ValueError("A origem da atividade não pode ficar vazia.")
        if normalized_source_type and normalized_source_id:
            existing = self.session.scalar(
                select(CustomerActivity).where(
                    CustomerActivity.customer_id == customer.id,
                    CustomerActivity.activity_type == activity_type,
                    CustomerActivity.source_type == normalized_source_type,
                    CustomerActivity.source_id == normalized_source_id,
                )
            )
            if existing is not None:
                return existing
        activity = CustomerActivity(
            customer_id=customer.id, activity_type=activity_type, description=description,
            created_by=user_id, metadata_json=metadata_json,
            source_type=normalized_source_type,
            source_id=normalized_source_id,
            source_reference=source_reference.strip()[:180] if source_reference else None,
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
        thresholds: InactivityThresholds | None = None,
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

        configured = thresholds or InactivityThresholds()
        if relationship == "NEVER":
            query = query.where(last_activity.is_(None))
        elif relationship == "RECENT":
            query = query.where(
                last_activity.is_not(None),
                last_activity > now - timedelta(days=configured.recent_days + 1),
            )
        elif (minimum_days := configured.minimum_days_for_filter(relationship)) is not None:
            query = query.where(
                last_activity.is_not(None),
                last_activity <= now - timedelta(days=minimum_days),
            )
        if inactive_days is not None:
            minimum_days = min(max(0, inactive_days), 365000) + 1
            query = query.where(
                last_activity.is_not(None),
                last_activity <= now - timedelta(days=minimum_days),
            )

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

    def indicators(
        self,
        *,
        month_start: datetime,
        stale_before: datetime | None = None,
        now: datetime | None = None,
        thresholds: InactivityThresholds | None = None,
    ) -> dict[str, int]:
        last_activity = self.last_relevant_activity_subquery()
        if stale_before is None:
            configured = thresholds or InactivityThresholds()
            current = now or datetime.now(month_start.tzinfo)
            stale_before = current - timedelta(days=configured.distant_days + 1)
        return {
            "total": int(self.session.scalar(select(func.count(Customer.id))) or 0),
            "active": int(self.session.scalar(select(func.count(Customer.id)).where(Customer.is_active.is_(True))) or 0),
            "new_month": int(self.session.scalar(select(func.count(Customer.id)).where(Customer.created_at >= month_start)) or 0),
            "long_inactive": int(self.session.scalar(
                select(func.count(Customer.id)).where(
                    last_activity.is_not(None), last_activity <= stale_before
                )
            ) or 0),
        }

    def relationship_counts(
        self, *, now: datetime, thresholds: InactivityThresholds
    ) -> dict[str, int]:
        last_activity = self.last_relevant_activity_subquery()

        def count_at_least(days: int) -> int:
            return int(self.session.scalar(
                select(func.count(Customer.id)).where(
                    last_activity.is_not(None),
                    last_activity <= now - timedelta(days=days),
                )
            ) or 0)

        return {
            "never": int(self.session.scalar(
                select(func.count(Customer.id)).where(last_activity.is_(None))
            ) or 0),
            "30_plus": count_at_least(thresholds.recent_days + 1),
            "60_plus": count_at_least(thresholds.attention_days + 1),
            "90_plus": count_at_least(thresholds.distant_days + 1),
        }

    def last_relevant_activity(self, customer_id: int) -> datetime | None:
        return self.session.scalar(
            select(func.max(CustomerActivity.occurred_at)).where(
                CustomerActivity.customer_id == customer_id,
                CustomerActivity.activity_type.in_(RELEVANT_ACTIVITY_TYPES),
            )
        )

    def last_service_summary(self, customer_id: int) -> LastServiceSummary | None:
        if not 0 < customer_id < 2**63:
            return None
        note = self.session.scalar(
            select(ServiceNote)
            .where(ServiceNote.customer_id == customer_id)
            .options(selectinload(ServiceNote.items))
            .order_by(ServiceNote.received_at.desc(), ServiceNote.id.desc())
            .limit(1)
        )
        if note is None:
            return None
        return LastServiceSummary(
            note_id=note.id,
            number=note.number_original,
            series=note.series_original,
            received_at=note.received_at,
            operational_status=note.operational_status,
            service_names=tuple(item.service_name_snapshot for item in note.items),
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

    def general_history_page(
        self, *, search: str = "", customer_id: int | None = None,
        activity_type: str = "ALL", user_id: int | None = None,
        date_from: datetime | None = None, date_to: datetime | None = None,
        page: int = 1, per_page: int = 25,
    ) -> tuple[list[ActivityItem], int, int]:
        conditions = []
        if search.strip():
            escaped = search.strip().casefold().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            like = f"%{escaped}%"
            conditions.append(or_(
                func.casefold(Customer.name).like(like, escape="\\"),
                func.casefold(CustomerActivity.description).like(like, escape="\\"),
            ))
        if customer_id:
            conditions.append(Customer.id == customer_id)
        if activity_type != "ALL":
            conditions.append(CustomerActivity.activity_type == activity_type)
        if user_id:
            conditions.append(User.id == user_id)
        if date_from:
            conditions.append(CustomerActivity.occurred_at >= date_from)
        if date_to:
            conditions.append(CustomerActivity.occurred_at <= date_to)

        base = (
            select(CustomerActivity, Customer, User.display_name)
            .join(Customer, Customer.id == CustomerActivity.customer_id)
            .join(User, User.id == CustomerActivity.created_by)
        )
        count_query = (
            select(func.count(CustomerActivity.id))
            .join(Customer, Customer.id == CustomerActivity.customer_id)
            .join(User, User.id == CustomerActivity.created_by)
        )
        if conditions:
            base = base.where(*conditions)
            count_query = count_query.where(*conditions)
        total = int(self.session.scalar(count_query) or 0)
        pages = max(1, (total + per_page - 1) // per_page)
        effective_page = min(max(1, page), pages)
        rows = self.session.execute(
            base.order_by(
                CustomerActivity.occurred_at.desc(), CustomerActivity.id.desc()
            )
            .offset((effective_page - 1) * per_page)
            .limit(per_page)
        ).all()
        return (
            [ActivityItem(activity=row[0], customer=row[1], actor_name=row[2]) for row in rows],
            total,
            effective_page,
        )

    def customer_history_page(
        self, customer_id: int, *, page: int = 1, per_page: int = 25
    ) -> tuple[list[CustomerActivity], int, int]:
        total = int(self.session.scalar(
            select(func.count(CustomerActivity.id)).where(
                CustomerActivity.customer_id == customer_id
            )
        ) or 0)
        pages = max(1, (total + per_page - 1) // per_page)
        effective_page = min(max(1, page), pages)
        rows = list(self.session.scalars(
            select(CustomerActivity)
            .where(CustomerActivity.customer_id == customer_id)
            .order_by(CustomerActivity.occurred_at.desc(), CustomerActivity.id.desc())
            .offset((effective_page - 1) * per_page)
            .limit(per_page)
        ))
        return rows, total, effective_page

    def customers_for_filter(self) -> list[Customer]:
        return list(self.session.scalars(select(Customer).order_by(Customer.name, Customer.id)))

    def users_for_filter(self) -> list[User]:
        return list(self.session.scalars(select(User).order_by(User.display_name, User.id)))
