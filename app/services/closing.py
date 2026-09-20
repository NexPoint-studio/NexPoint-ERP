from __future__ import annotations

from datetime import datetime, timezone
import json
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.models import AuditEvent, ServiceNoteEvent
from app.models.receivables import CustomerReceivable, NoteClosure
from app.observability.context import emit_observability_event
from app.repositories.payments import PaymentRepository
from app.repositories.receivables import ReceivableRepository
from app.services.authorization import require_active_actor_permission
from app.services.transactions import atomic_write


class NoteClosingError(RuntimeError): pass
class NoteClosingConflict(NoteClosingError): pass


class NoteClosingService:
    def __init__(self, session: Session):
        self.session = session
        self.payments = PaymentRepository(session)
        self.receivables = ReceivableRepository(session)

    @atomic_write
    def close(self, note_id: int, request_uid: str, actor_id: int) -> NoteClosure:
        if self.session.in_transaction():
            raise RuntimeError("O fechamento deve iniciar uma transação exclusiva.")
        self.session.connection().exec_driver_sql("begin immediate")
        require_active_actor_permission(self.session, actor_id, "notes.change_status")
        emit_observability_event(
            module="note", component="closing_service",
            event_type="note.close.requested", operation="close",
            status="started", user_id=actor_id, sync_required=True,
        )
        try:
            normalized_uid = str(UUID(str(request_uid)))
        except (ValueError, TypeError, AttributeError):
            raise NoteClosingError("Identificação de fechamento inválida.")
        if normalized_uid != str(request_uid):
            raise NoteClosingError("Identificação de fechamento inválida.")
        by_uid = self.receivables.closure_by_uid(normalized_uid)
        if by_uid is not None:
            if by_uid.service_note_id != note_id:
                raise NoteClosingConflict("Esta identificação já foi usada em outra Nota.")
            self.session.commit()
            return by_uid
        note = self.payments.note(note_id)
        if note is None:
            raise NoteClosingError("Nota não encontrada.")
        if note.operational_status == "CANCELADO":
            raise NoteClosingError("Notas canceladas não podem ser fechadas.")
        if self.receivables.closure(note_id) is not None:
            raise NoteClosingConflict("Esta Nota já foi fechada.")
        paid = self.payments.paid_cents_for_note(note_id)
        if paid > note.total_cents:
            raise NoteClosingError("Os pagamentos superam o total da Nota.")
        outstanding = note.total_cents - paid
        emit_observability_event(
            module="payment", component="closing_service",
            event_type="payment.validation.ok", operation="validate_balance",
            status="completed", user_id=actor_id, sync_required=True,
        )
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        closure = NoteClosure(
            request_uid=normalized_uid, service_note_id=note.id,
            total_amount_cents=note.total_cents, paid_amount_cents=paid,
            outstanding_amount_cents=outstanding, closed_at=now, closed_by=actor_id,
        )
        self.session.add(closure)
        note.operational_status = "FECHADO"
        note.revision += 1
        note.updated_by = actor_id
        note.updated_at = now
        if outstanding:
            receivable = CustomerReceivable(
                customer_id=note.customer_id, source_note_id=note.id,
                original_amount_cents=outstanding, remaining_amount_cents=outstanding,
                status="OPEN", created_at=now, updated_at=now,
            )
            self.session.add(receivable)
            self.session.flush()
            self.session.add(AuditEvent(
                user_id=actor_id,
                action="receivable.created",
                resource=f"customer_receivables/{receivable.id}",
                details=json.dumps({
                    "source_note_id": note.id,
                    "original_amount_cents": outstanding,
                }, sort_keys=True),
            ))
            emit_observability_event(
                module="receivable", component="closing_service",
                event_type="receivable.created", operation="create",
                status="completed", user_id=actor_id, sync_required=True,
            )
        details = {"total_amount_cents": note.total_cents, "paid_amount_cents": paid,
                   "outstanding_amount_cents": outstanding}
        self.session.add(ServiceNoteEvent(
            event_uid=str(uuid4()), note_id=note.id, event_type="NOTE_CLOSED",
            occurred_at=now, created_by=actor_id,
            details_json=json.dumps(details, ensure_ascii=False, sort_keys=True),
        ))
        self.session.add(AuditEvent(user_id=actor_id, action="service_note.closed",
                                    resource=f"service_notes/{note.id}",
                                    details=json.dumps(details, sort_keys=True)))
        self.session.commit()
        emit_observability_event(
            module="note", component="closing_service",
            event_type="note.closed", operation="close",
            status="completed", user_id=actor_id, sync_required=True,
        )
        return closure
