"""Recover the boundary between durable sidecar bytes and operational outbox."""
from dataclasses import replace
from types import SimpleNamespace
import logging
import sqlite3
from sqlalchemy.exc import SQLAlchemyError

from app.repositories.sync import OutboxRepository
from app.services.support_tickets import ClientSupportService, SupportTicketNotFoundError, client_support_identity, ensure_control_center_repository
from app.services.support_conversation import cached_ticket
from control_center.domain import ControlCenterError, ControlCenterConflictError, ControlCenterNotFoundError


def ensure_local_attachment_ticket(repository, identity, outbox, ticket_id):
    existing = repository.get_tenant_ticket(identity.tenant_id,ticket_id)
    if existing is not None:
        if existing.installation_id != identity.installation_id:
            raise ControlCenterNotFoundError("Chamado nao encontrado.")
        return existing
    service = ClientSupportService(repository,identity,outbox)
    try:
        source = service.snapshot(ticket_id).selected_ticket
    except SupportTicketNotFoundError:
        source = None
    if source is None:
        cached = repository.get_cached_public_ticket(identity.tenant_id,identity.installation_id,ticket_id)
        if cached is None:
            raise ControlCenterNotFoundError("Chamado nao encontrado.")
        source = cached_ticket(cached,identity)
    service.ensure_registration()
    mirror = replace(source,tenant_id=identity.tenant_id,installation_id=identity.installation_id,
                     created_by=identity.actor_ref,internal_notes=(),history=())
    try:
        return repository.create_ticket_for_tenant(identity.tenant_id,mirror)
    except ControlCenterConflictError:
        existing = repository.get_tenant_ticket(identity.tenant_id,ticket_id)
        if existing is None or existing.installation_id != identity.installation_id:
            raise ControlCenterNotFoundError("Chamado nao encontrado.") from None
        return existing


def enqueue_attachment_reference(outbox, identity, ticket_id, attachment_id):
    return outbox.enqueue(event_type="support_operation",aggregate_type="support_ticket",
        aggregate_id=attachment_id,idempotency_key="support-attachment:"+attachment_id,
        payload=dict(action="attachment",attachment_id=attachment_id,ticket_id=ticket_id,
                     tenant_id=identity.tenant_id,installation_id=identity.installation_id,
                     correlation_id=attachment_id))


class AttachmentOutboxReconciler:
    """Bounded rotating sweep; restart safely begins again from the first ID."""
    def __init__(self,app):
        self.app = app
        self.after_id = ""
        self.scope = None

    def run(self):
        try:
            return self._run()
        except (ControlCenterError,sqlite3.Error,OSError,SQLAlchemyError,ValueError) as exc:
            logging.getLogger("erp.support").warning(
                "Reconciliacao de anexos pendente; nova tentativa no proximo ciclo (%s).",
                type(exc).__name__)
            return 0

    def _run(self):
        repository = ensure_control_center_repository(self.app)
        with self.app.state.session_factory() as session:
            identity = client_support_identity(SimpleNamespace(app=self.app),session)
            scope = (identity.tenant_id,identity.installation_id)
            if scope != self.scope:
                self.scope,self.after_id = scope,""
            refs = repository.list_undelivered_attachment_references(*scope,after_id=self.after_id)
            outbox = OutboxRepository(session)
            for attachment in refs:
                enqueue_attachment_reference(outbox,identity,attachment.ticket_id,attachment.id)
            session.commit()
            self.after_id = refs[-1].id if len(refs) == 100 else ""
            return len(refs)
