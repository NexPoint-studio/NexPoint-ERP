from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
import math
from unicodedata import normalize

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.models import CashCategory, CashMovement, CashPaymentMethod


@dataclass(frozen=True, slots=True)
class CashCategoryListItem:
    category: CashCategory
    movement_count: int


@dataclass(frozen=True, slots=True)
class ReportLabel:
    name: str


@dataclass(frozen=True, slots=True)
class CashReportMovement:
    """Projeção financeira: não carrega usuários, permissões ou histórico ORM."""
    movement_type: str
    gross_amount: Decimal
    fee_amount: Decimal
    net_amount: Decimal
    occurred_at: datetime
    payment_method_id: int | None
    category: ReportLabel | None
    payment_method: ReportLabel | None
    status: str = "ACTIVE"


class CashCategoryRepository:
    def __init__(self, session: Session):
        self.session = session

    def get(self, category_id: int) -> CashCategory | None:
        if not 0 < category_id < 2**63:
            return None
        return self.session.get(CashCategory, category_id)

    def by_name(self, name: str, exclude_id: int | None = None) -> CashCategory | None:
        # SQLite's lower() only performs reliable case conversion for ASCII.
        # Compare normalized values in Python so names such as "Água" and
        # "água" (including composed/decomposed Unicode forms) are duplicates.
        normalized_name = normalize("NFKC", name).casefold()
        query = select(CashCategory)
        if exclude_id is not None:
            query = query.where(CashCategory.id != exclude_id)
        return next(
            (
                category
                for category in self.session.scalars(query)
                if normalize("NFKC", category.name).casefold() == normalized_name
            ),
            None,
        )

    def linked_movement_types(self, category_id: int) -> set[str]:
        return set(self.session.scalars(
            select(CashMovement.movement_type)
            .where(CashMovement.category_id == category_id)
            .distinct()
        ))

    def all(self, *, active_only: bool = False, movement_type: str | None = None) -> list[CashCategory]:
        query = select(CashCategory).order_by(CashCategory.sort_order, CashCategory.name)
        if active_only:
            query = query.where(CashCategory.is_active.is_(True))
        if movement_type in {"ENTRY", "EXIT"}:
            query = query.where(CashCategory.movement_type.in_((movement_type, "BOTH")))
        return list(self.session.scalars(query))

    def list_with_counts(self) -> list[CashCategoryListItem]:
        rows = self.session.execute(
            select(CashCategory, func.count(CashMovement.id))
            .outerjoin(CashMovement)
            .group_by(CashCategory.id)
            .order_by(CashCategory.sort_order, CashCategory.name)
        ).all()
        return [CashCategoryListItem(row[0], int(row[1])) for row in rows]


class PaymentMethodRepository:
    def __init__(self, session: Session):
        self.session = session

    def get(self, payment_method_id: int) -> CashPaymentMethod | None:
        if not 0 < payment_method_id < 2**63:
            return None
        return self.session.get(CashPaymentMethod, payment_method_id)

    def all(self, *, active_only: bool = False) -> list[CashPaymentMethod]:
        query = select(CashPaymentMethod).order_by(CashPaymentMethod.sort_order, CashPaymentMethod.name)
        if active_only:
            query = query.where(CashPaymentMethod.is_active.is_(True))
        return list(self.session.scalars(query))


class CashMovementRepository:
    def __init__(self, session: Session):
        self.session = session

    @staticmethod
    def _with_relations(query):
        return query.options(
            selectinload(CashMovement.category),
            selectinload(CashMovement.payment_method),
            selectinload(CashMovement.creator),
            selectinload(CashMovement.updater),
            selectinload(CashMovement.canceler),
        )

    def get(self, movement_id: int) -> CashMovement | None:
        if not 0 < movement_id < 2**63:
            return None
        return self.session.scalar(self._with_relations(select(CashMovement).where(CashMovement.id == movement_id)))

    def list(
        self,
        *,
        start_utc: datetime,
        end_utc: datetime,
        search: str = "",
        movement_type: str = "ALL",
        status: str = "ALL",
        category: str = "ALL",
        payment: str = "ALL",
        sort: str = "recent",
        page: int = 1,
        per_page: int = 25,
    ) -> tuple[list[CashMovement], int, int]:
        base_query = (
            select(CashMovement)
            .outerjoin(CashCategory, CashMovement.category_id == CashCategory.id)
            .outerjoin(CashPaymentMethod, CashMovement.payment_method_id == CashPaymentMethod.id)
            .where(CashMovement.occurred_at >= start_utc, CashMovement.occurred_at < end_utc)
        )
        term = search.strip().casefold()
        if term:
            # Treat user input literally: %, _ and the escape character must not
            # acquire SQL LIKE wildcard semantics.
            escaped_term = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            like = f"%{escaped_term}%"
            base_query = base_query.where(or_(
                func.casefold(CashMovement.description).like(like, escape="\\"),
                func.casefold(func.coalesce(CashMovement.notes, "")).like(like, escape="\\"),
                func.casefold(func.coalesce(CashCategory.name, "")).like(like, escape="\\"),
                func.casefold(func.coalesce(CashPaymentMethod.name, "")).like(like, escape="\\"),
            ))
        if movement_type in {"ENTRY", "EXIT"}:
            base_query = base_query.where(CashMovement.movement_type == movement_type)
        if status in {"ACTIVE", "CANCELED"}:
            base_query = base_query.where(CashMovement.status == status)
        if category == "NONE":
            base_query = base_query.where(CashMovement.category_id.is_(None))
        elif category.isascii() and category.isdecimal() and len(category) < 19:
            base_query = base_query.where(CashMovement.category_id == int(category))
        if payment == "NONE":
            base_query = base_query.where(CashMovement.payment_method_id.is_(None))
        elif payment.isascii() and payment.isdecimal() and len(payment) < 19:
            base_query = base_query.where(CashMovement.payment_method_id == int(payment))

        count_query = select(func.count()).select_from(base_query.order_by(None).subquery())
        total = int(self.session.scalar(count_query) or 0)
        pages = max(1, math.ceil(total / per_page))
        effective_page = min(max(1, page), pages)
        ordering = {
            "recent": (CashMovement.occurred_at.desc(), CashMovement.id.desc()),
            "oldest": (CashMovement.occurred_at.asc(), CashMovement.id.asc()),
            "value_desc": (CashMovement.net_amount.desc(), CashMovement.occurred_at.desc(), CashMovement.id.desc()),
            "value_asc": (CashMovement.net_amount.asc(), CashMovement.occurred_at.desc(), CashMovement.id.desc()),
        }.get(sort, (CashMovement.occurred_at.desc(), CashMovement.id.desc()))
        rows = list(self.session.scalars(
            self._with_relations(base_query)
            .order_by(*ordering)
            .offset((effective_page - 1) * per_page)
            .limit(per_page)
        ).unique())
        return rows, total, effective_page

    def active_before(self, cutoff_utc: datetime):
        return self.session.execute(select(
            CashMovement.status, CashMovement.movement_type,
            CashMovement.gross_amount, CashMovement.fee_amount, CashMovement.net_amount,
        ).where(CashMovement.status == "ACTIVE", CashMovement.occurred_at < cutoff_utc)).all()

    def active_between(self, start_utc: datetime, end_utc: datetime) -> list[CashReportMovement]:
        query = select(
            CashMovement.movement_type, CashMovement.gross_amount,
            CashMovement.fee_amount, CashMovement.net_amount, CashMovement.occurred_at,
            CashMovement.payment_method_id, CashCategory.name, CashPaymentMethod.name,
        ).outerjoin(CashCategory, CashMovement.category_id == CashCategory.id).outerjoin(
            CashPaymentMethod, CashMovement.payment_method_id == CashPaymentMethod.id,
        ).where(
                CashMovement.status == "ACTIVE",
                CashMovement.occurred_at >= start_utc,
                CashMovement.occurred_at < end_utc,
        ).order_by(CashMovement.occurred_at, CashMovement.id)
        return [CashReportMovement(*row[:6], ReportLabel(row[6]) if row[6] else None,
                                   ReportLabel(row[7]) if row[7] else None)
                for row in self.session.execute(query)]

    def recent(self, cutoff_utc: datetime, limit: int = 8) -> list[CashMovement]:
        return list(self.session.scalars(self._with_relations(
            select(CashMovement).where(
                CashMovement.status == "ACTIVE",
                CashMovement.occurred_at <= cutoff_utc,
            ).order_by(CashMovement.occurred_at.desc(), CashMovement.id.desc()).limit(limit)
        )).unique())

    def count(self) -> int:
        return int(self.session.scalar(select(func.count(CashMovement.id))) or 0)
