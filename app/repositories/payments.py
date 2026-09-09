"""Consultas do fluxo transacional de pagamentos."""
from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from app.models import (
    AuditEvent,
    CashMovement,
    CashPaymentMethod,
    Payment,
    PaymentFeeRule,
    PaymentTerminal,
    ServiceNote,
)


class PaymentRepository:
    def __init__(self, session: Session):
        self.session = session

    @staticmethod
    def _valid_id(value: int) -> bool:
        return isinstance(value, int) and not isinstance(value, bool) and 0 < value < 2**63

    def note(self, note_id: int) -> ServiceNote | None:
        if not self._valid_id(note_id):
            return None
        return self.session.scalar(
            select(ServiceNote)
            .where(ServiceNote.id == note_id)
            .options(selectinload(ServiceNote.payments))
            .execution_options(populate_existing=True)
        )

    def by_request_uid(self, request_uid: str) -> Payment | None:
        return self.session.scalar(
            select(Payment).where(Payment.request_uid == request_uid).limit(1)
        )

    def payment_methods(self, *, active_only: bool = True) -> list[CashPaymentMethod]:
        query = select(CashPaymentMethod)
        if active_only:
            query = query.where(CashPaymentMethod.is_active.is_(True))
        return list(self.session.scalars(query.order_by(
            CashPaymentMethod.sort_order, CashPaymentMethod.name, CashPaymentMethod.id
        )))

    def terminals(self, *, active_only: bool = True) -> list[PaymentTerminal]:
        query = select(PaymentTerminal)
        if active_only:
            query = query.where(PaymentTerminal.is_active.is_(True))
        return list(self.session.scalars(query.order_by(
            PaymentTerminal.sort_order, PaymentTerminal.name, PaymentTerminal.id
        )))

    def confirmed_for_note(self, note_id: int) -> Payment | None:
        if not self._valid_id(note_id):
            return None
        return self.session.scalar(
            select(Payment).where(
                Payment.service_note_id == note_id,
                Payment.status == "CONFIRMED",
            ).limit(1)
        )

    def payment_method(self, method_id: int | None) -> CashPaymentMethod | None:
        if method_id is None or not self._valid_id(method_id):
            return None
        return self.session.get(CashPaymentMethod, method_id)

    def terminal(self, terminal_id: int | None) -> PaymentTerminal | None:
        if terminal_id is None or not self._valid_id(terminal_id):
            return None
        return self.session.get(PaymentTerminal, terminal_id)

    def resolve_fee_rule(
        self,
        *,
        payment_method_id: int,
        terminal_id: int | None,
        card_mode: str | None,
        installments: int | None,
        paid_at: datetime,
    ) -> PaymentFeeRule | None:
        conditions = [
            PaymentFeeRule.payment_method_id == payment_method_id,
            # Regras encerradas continuam válidas dentro do intervalo em que
            # estiveram vigentes. Uma regra inativa sem fim de vigência é um
            # rascunho/futuro desativado e nunca participa do cálculo.
            or_(
                PaymentFeeRule.is_active.is_(True),
                PaymentFeeRule.valid_until.is_not(None),
            ),
            PaymentFeeRule.valid_from <= paid_at,
            or_(PaymentFeeRule.valid_until.is_(None), PaymentFeeRule.valid_until > paid_at),
        ]
        if terminal_id is None:
            conditions.append(PaymentFeeRule.terminal_id.is_(None))
        else:
            conditions.append(or_(
                PaymentFeeRule.terminal_id.is_(None),
                PaymentFeeRule.terminal_id == terminal_id,
            ))
        if card_mode is None:
            conditions.append(PaymentFeeRule.card_mode.is_(None))
        else:
            conditions.append(or_(
                PaymentFeeRule.card_mode.is_(None),
                PaymentFeeRule.card_mode == card_mode,
            ))
        if installments is None:
            conditions.append(PaymentFeeRule.installments.is_(None))
        else:
            conditions.append(or_(
                PaymentFeeRule.installments.is_(None),
                PaymentFeeRule.installments == installments,
            ))

        candidates = list(self.session.scalars(
            select(PaymentFeeRule).where(*conditions)
        ))
        if not candidates:
            return None

        def specificity(rule: PaymentFeeRule) -> tuple[int, int, int, int, datetime, int]:
            terminal_specific = int(rule.terminal_id is not None)
            mode_specific = int(rule.card_mode is not None)
            installment_specific = int(rule.installments is not None)
            return (
                terminal_specific + mode_specific + installment_specific,
                installment_specific,
                mode_specific,
                terminal_specific,
                rule.valid_from,
                rule.id,
            )

        return max(candidates, key=specificity)

    def cash_for_payment(self, payment_id: int) -> CashMovement | None:
        if not self._valid_id(payment_id):
            return None
        return self.session.scalar(select(CashMovement).where(
            CashMovement.source_type == "PAYMENT",
            CashMovement.source_id == str(payment_id),
        ).limit(1))

    def request_fingerprint(self, payment_id: int) -> dict[str, object] | None:
        if not self._valid_id(payment_id):
            return None
        raw = self.session.scalar(
            select(AuditEvent.details)
            .where(
                AuditEvent.action == "payment.confirmed",
                AuditEvent.resource == f"payments/{payment_id}",
            )
            .order_by(AuditEvent.id.desc())
            .limit(1)
        )
        if not raw:
            return None
        try:
            details = json.loads(raw)
        except (TypeError, ValueError):
            return None
        fingerprint = details.get("request_fingerprint") if isinstance(details, dict) else None
        return fingerprint if isinstance(fingerprint, dict) else None
