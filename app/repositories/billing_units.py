from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.services import BillingUnit


class BillingUnitRepository:
    def __init__(self, session: Session):
        self.session = session

    def get(self, billing_unit_id: int) -> BillingUnit | None:
        if not 0 < billing_unit_id < 2**63:
            return None
        return self.session.get(BillingUnit, billing_unit_id)

    def by_code(self, code: str) -> BillingUnit | None:
        normalized = str(code or "").strip().upper()
        if not normalized:
            return None
        return self.session.scalar(select(BillingUnit).where(BillingUnit.code == normalized))

    def all(self, *, active_only: bool = False) -> list[BillingUnit]:
        query = select(BillingUnit).order_by(
            BillingUnit.display_order, BillingUnit.name, BillingUnit.id
        )
        if active_only:
            query = query.where(BillingUnit.is_active.is_(True))
        return list(self.session.scalars(query))
