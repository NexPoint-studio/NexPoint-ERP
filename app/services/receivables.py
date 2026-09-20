from __future__ import annotations

from datetime import datetime, timezone
import json
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.core.money import MAX_CASH_CENTS, cents_to_decimal, decimal_to_cents
from app.models import AuditEvent, CashMovement, CashPaymentMethod, ServiceNote, ServiceNoteEvent
from app.models.receivables import NoteReceivableLink, ReceivablePayment
from app.observability.context import emit_observability_event
from app.repositories.receivables import ReceivableRepository
from app.services.authorization import require_active_actor_permission
from app.services.transactions import atomic_write


class ReceivableError(RuntimeError): pass


class ReceivableService:
    def __init__(self, session: Session):
        self.session = session
        self.repository = ReceivableRepository(session)

    def refresh_source_note_status(self, receivable, actor_id: int, now: datetime) -> None:
        source_note = self.session.get(ServiceNote, receivable.source_note_id)
        if source_note is None or source_note.customer_id != receivable.customer_id:
            raise ReceivableError("A origem do saldo devedor está inconsistente.")
        new_status = "PAGO" if receivable.remaining_amount_cents == 0 else "PARCIAL"
        if source_note.financial_status == new_status:
            return
        previous_status = source_note.financial_status
        source_note.financial_status = new_status
        source_note.financial_settlement_reason = "PAYMENT" if new_status == "PAGO" else None
        source_note.revision += 1
        source_note.updated_by = actor_id
        source_note.updated_at = now
        details = {
            "receivable_id": receivable.id,
            "from": previous_status,
            "to": new_status,
            "remaining_amount_cents": receivable.remaining_amount_cents,
        }
        self.session.add(ServiceNoteEvent(
            event_uid=str(uuid4()), note_id=source_note.id,
            event_type="RECEIVABLE_SETTLED", occurred_at=now,
            created_by=actor_id, details_json=json.dumps(details, sort_keys=True),
        ))
        self.session.add(AuditEvent(
            user_id=actor_id, action="service_note.receivable_settled",
            resource=f"service_notes/{source_note.id}",
            details=json.dumps(details, sort_keys=True),
        ))

    @atomic_write
    def link_to_note(self, note_id: int, receivable_ids: list[int], actor_id: int) -> list[NoteReceivableLink]:
        require_active_actor_permission(self.session, actor_id, "notes.edit")
        note = self.session.get(ServiceNote, note_id)
        if note is None:
            raise ReceivableError("Nota não encontrada.")
        created = []
        for receivable_id in dict.fromkeys(receivable_ids):
            receivable = self.repository.get(receivable_id)
            if receivable is None or receivable.status == "SETTLED":
                raise ReceivableError("Saldo anterior inválido ou já liquidado.")
            if receivable.customer_id != note.customer_id or receivable.source_note_id == note.id:
                raise ReceivableError("O saldo anterior não pertence ao cliente desta Nota.")
            link = NoteReceivableLink(note_id=note_id, receivable_id=receivable.id,
                                      amount_snapshot_cents=receivable.remaining_amount_cents,
                                      created_by=actor_id)
            self.session.add(link); created.append(link)
        self.session.commit()
        return created

    @atomic_write
    def receive_debt(self, receivable_id: int, amount_cents: int, method_id: int,
                     paid_at: datetime, request_uid: str, actor_id: int) -> ReceivablePayment:
        if self.session.in_transaction():
            raise RuntimeError("O recebimento deve iniciar uma transação exclusiva.")
        self.session.connection().exec_driver_sql("begin immediate")
        require_active_actor_permission(self.session, actor_id, "payments.receive")
        emit_observability_event(
            module="receivable", component="receivable_service",
            event_type="receivable.payment_requested", operation="receive_debt",
            status="started", user_id=actor_id, sync_required=True,
        )
        try:
            normalized_uid = str(UUID(str(request_uid)))
        except (TypeError, ValueError, AttributeError):
            raise ReceivableError("Identificação de pagamento inválida.") from None
        if normalized_uid != request_uid:
            raise ReceivableError("Identificação de pagamento inválida.")
        if not isinstance(amount_cents, int) or isinstance(amount_cents, bool):
            raise ReceivableError("Valor do pagamento inválido.")
        if not isinstance(paid_at, datetime):
            raise ReceivableError("Data do pagamento inválida.")
        normalized_paid_at = paid_at.astimezone(timezone.utc).replace(tzinfo=None) if paid_at.tzinfo else paid_at
        existing = self.repository.debt_payment_by_uid(request_uid)
        if existing is not None:
            if (existing.receivable_id != receivable_id or existing.amount_cents != amount_cents
                or existing.payment_method_id != method_id or existing.paid_at != normalized_paid_at):
                raise ReceivableError("Identificação de pagamento reutilizada com dados diferentes.")
            self.session.commit()
            emit_observability_event(
                module="receivable", component="receivable_service",
                event_type="payment.idempotent_duplicate", operation="receive_debt",
                status="idempotent", user_id=actor_id,
                severity="WARNING", error_code="duplicate_request",
                sync_required=True,
            )
            emit_observability_event(
                module="cash", component="receivable_service",
                event_type="cash.entry.prevented_duplicate",
                operation="receivable_entry", status="prevented",
                user_id=actor_id, severity="WARNING",
                error_code="duplicate_request", sync_required=True,
            )
            return existing
        receivable = self.repository.get(receivable_id)
        method = self.session.get(CashPaymentMethod, method_id)
        if receivable is None or receivable.status == "SETTLED": raise ReceivableError("Débito não está em aberto.")
        if method is None or not method.is_active or method.method_kind == "CARD":
            raise ReceivableError("Selecione uma forma de pagamento sem terminal de cartão.")
        if amount_cents <= 0 or amount_cents > receivable.remaining_amount_cents:
            raise ReceivableError("Valor inválido para o saldo deste débito.")
        if amount_cents > MAX_CASH_CENTS:
            raise ReceivableError("O valor recebido excede o limite monetário do Caixa.")
        source_note = self.session.get(ServiceNote, receivable.source_note_id)
        if source_note is None or source_note.customer_id != receivable.customer_id:
            raise ReceivableError("A origem do saldo devedor está inconsistente.")
        source_received_at = source_note.received_at
        if source_received_at.tzinfo is not None:
            source_received_at = source_received_at.astimezone(timezone.utc).replace(tzinfo=None)
        if normalized_paid_at < source_received_at:
            raise ReceivableError("A data do pagamento não pode anteceder o recebimento da Nota de origem.")
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        payment = ReceivablePayment(request_uid=request_uid, receivable_id=receivable.id,
            payment_method_id=method.id, method_name_snapshot=method.name, amount_cents=amount_cents,
            paid_at=normalized_paid_at, created_by=actor_id, created_at=now)
        self.session.add(payment); self.session.flush()
        receivable.remaining_amount_cents -= amount_cents
        receivable.status = "SETTLED" if receivable.remaining_amount_cents == 0 else "PARTIALLY_PAID"
        receivable.updated_at = now
        self.refresh_source_note_status(receivable, actor_id, now)
        movement = CashMovement(movement_type="ENTRY", description="Pagamento de saldo devedor",
            category_id=None, payment_method_id=method.id, gross_amount=cents_to_decimal(amount_cents),
            fee_amount=cents_to_decimal(0), net_amount=cents_to_decimal(amount_cents), occurred_at=normalized_paid_at,
            notes=None, status="ACTIVE", origin="SYSTEM", source_reference=f"Débito #{receivable.id}",
            source_type="RECEIVABLE_PAYMENT", source_id=str(payment.id), created_at=now, updated_at=now,
            created_by=actor_id, updated_by=actor_id)
        self.session.add(movement)
        self.session.flush()
        self.session.expire(movement, ["gross_amount", "fee_amount", "net_amount"])
        if (
            decimal_to_cents(movement.gross_amount),
            decimal_to_cents(movement.fee_amount),
            decimal_to_cents(movement.net_amount),
        ) != (amount_cents, 0, amount_cents):
            raise ReceivableError("O Caixa não preservou os centavos do recebimento.")
        self.session.add(AuditEvent(user_id=actor_id, action="receivable.payment_received",
            resource=f"customer_receivables/{receivable.id}",
            details=json.dumps({"payment_id": payment.id, "amount_cents": amount_cents})))
        self.session.commit()
        emit_observability_event(
            module="receivable", component="receivable_service",
            event_type=("receivable.settled" if receivable.status == "SETTLED"
                        else "receivable.updated"),
            operation="receive_debt", status="completed",
            user_id=actor_id, sync_required=True,
        )
        emit_observability_event(
            module="cash", component="receivable_service",
            event_type="cash.entry.created", operation="receivable_entry",
            status="completed", user_id=actor_id, sync_required=True,
        )
        return payment
