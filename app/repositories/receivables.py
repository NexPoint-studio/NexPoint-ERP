from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.receivables import CustomerReceivable, NoteClosure, NoteReceivableLink, PaymentAllocation, ReceivablePayment


class ReceivableRepository:
    def __init__(self, session: Session): self.session = session

    def closure(self, note_id: int) -> NoteClosure | None:
        return self.session.scalar(select(NoteClosure).where(NoteClosure.service_note_id == note_id))

    def closure_by_uid(self, uid: str) -> NoteClosure | None:
        return self.session.scalar(select(NoteClosure).where(NoteClosure.request_uid == uid))

    def open_for_customer(self, customer_id: int) -> list[CustomerReceivable]:
        return list(self.session.scalars(select(CustomerReceivable).where(
            CustomerReceivable.customer_id == customer_id,
            CustomerReceivable.status != "SETTLED",
        ).order_by(CustomerReceivable.created_at, CustomerReceivable.id)))

    def for_note(self, note_id: int) -> CustomerReceivable | None:
        return self.session.scalar(select(CustomerReceivable).where(CustomerReceivable.source_note_id == note_id))

    def get(self, receivable_id: int) -> CustomerReceivable | None:
        return self.session.get(CustomerReceivable, receivable_id)

    def debt_payment_by_uid(self, uid: str) -> ReceivablePayment | None:
        return self.session.scalar(select(ReceivablePayment).where(ReceivablePayment.request_uid == uid))

    def payments_for(self, receivable_id: int) -> list[ReceivablePayment]:
        return list(self.session.scalars(
            select(ReceivablePayment)
            .where(ReceivablePayment.receivable_id == receivable_id)
            .order_by(ReceivablePayment.paid_at, ReceivablePayment.id)
        ))

    def links(self, note_id: int) -> list[NoteReceivableLink]:
        return list(self.session.scalars(select(NoteReceivableLink).where(NoteReceivableLink.note_id == note_id)))

    def total_open(self, customer_id: int) -> int:
        return int(self.session.scalar(select(func.coalesce(func.sum(CustomerReceivable.remaining_amount_cents), 0)).where(
            CustomerReceivable.customer_id == customer_id, CustomerReceivable.status != "SETTLED")) or 0)

    def allocated_to_note(self, payment_id: int) -> int:
        return int(self.session.scalar(select(func.coalesce(func.sum(PaymentAllocation.amount_cents), 0)).where(
            PaymentAllocation.payment_id == payment_id, PaymentAllocation.receivable_id.is_(None))) or 0)

    def allocations_for_payment(self, payment_id: int) -> list[PaymentAllocation]:
        return list(self.session.scalars(select(PaymentAllocation).where(
            PaymentAllocation.payment_id == payment_id,
        ).order_by(PaymentAllocation.id)))
