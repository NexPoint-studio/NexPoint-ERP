"""Public conversation delivery using the existing durable operational Outbox."""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from app.services.sync_remote import SyncAck, SyncRemoteError
from control_center.domain import ControlCenterError, SupportTicket
from control_center.sanitization import sanitize_text


class SupportSyncRemote:
    def __init__(self, base, repository_factory, tenant_id, installation_id, remote=None, *, attachments_remote=None):
        self.base, self.repository_factory = base, repository_factory
        self.tenant_id, self.installation_id, self.remote = tenant_id, installation_id, remote
        self.attachments_remote = attachments_remote

    def send_batch(self, items):
        items = tuple(items)
        if all(item.event_type != "support_operation" for item in items):
            return self.base.send_batch(items)
        acks = []
        for item in items:
            if item.event_type != "support_operation":
                acks.extend(self.base.send_batch((item,)))
                continue
            payload = item.payload
            if (payload.get("tenant_id") != self.tenant_id
                    or payload.get("installation_id") != self.installation_id):
                raise SyncRemoteError("Identidade do suporte inválida.", retryable=False)
            try:
                repo = self.repository_factory()
                if payload["action"] == "attachment":
                    attachment = repo.get_ticket_attachment(payload["ticket_id"], payload["attachment_id"],
                        tenant_id=self.tenant_id, installation_id=self.installation_id)
                    if attachment is None:
                        raise SyncRemoteError("Anexo local nao encontrado.", retryable=False)
                    if self.attachments_remote is not None:
                        self.attachments_remote.upload(payload["ticket_id"], attachment)
                    elif self.remote is not None:
                        raise SyncRemoteError("Transporte de anexos indisponivel.")
                    repo.mark_ticket_attachment_delivered(payload["ticket_id"],payload["attachment_id"],
                        tenant_id=self.tenant_id,installation_id=self.installation_id)
                elif self.remote is not None:
                    result = self.remote.call(payload["action"], ticket_id=payload["ticket_id"],
                        operation_id=payload["operation_id"], body=payload.get("body", ""),
                        status=payload.get("status", ""))
                    for ticket in result["tickets"]:
                        repo.cache_public_ticket(self.tenant_id, self.installation_id, ticket["ticket_id"], ticket)
                elif payload["action"] == "reply":
                    repo.add_ticket_message(payload["ticket_id"], payload["body"], "client",
                        message_id=payload["operation_id"], direction="customer",
                        tenant_id=self.tenant_id, installation_id=self.installation_id)
                elif payload["action"] == "status":
                    repo.set_ticket_status(payload["ticket_id"], payload["status"],
                        changed_by="client", tenant_id=self.tenant_id,
                        installation_id=self.installation_id, operation_id=payload["operation_id"])
                else:
                    raise ValueError("Ação de suporte inválida.")
            except SyncRemoteError:
                raise
            except (KeyError, ValueError, TypeError):
                raise SyncRemoteError("Operação de suporte inválida.", retryable=False) from None
            except ControlCenterError:
                raise SyncRemoteError("Não foi possível aplicar a operação de suporte.") from None
            acks.append(SyncAck(item.idempotency_key, item.aggregate_id, item.schema_version))
        return tuple(acks)


def enqueue_operation(outbox, identity, raw, ticket_id):
    action = str(raw.get("action", ""))
    operation_id = str(raw.get("operation_id", ""))
    try:
        if str(UUID(operation_id)) != operation_id:
            raise ValueError()
    except (ValueError, TypeError):
        raise ValueError("Atualize o chamado antes de enviar.") from None
    if action not in {"reply", "status"}:
        raise ValueError("Ação inválida.")
    body = str(raw.get("body", "")).strip()
    status = str(raw.get("status", ""))
    if action == "reply" and not 1 <= len(body) <= 4000:
        raise ValueError("Escreva uma mensagem de até 4000 caracteres.")
    if action == "status" and status not in {"open", "resolved", "closed"}:
        raise ValueError("Estado inválido.")
    payload = dict(tenant_id=identity.tenant_id, installation_id=identity.installation_id,
        action=action, ticket_id=ticket_id, operation_id=operation_id,
        body=sanitize_text(body, maximum=4000) if action == "reply" else "",
        status=status if action == "status" else "")
    # Identical retries have identical payload even across HTTP correlation IDs.
    payload["correlation_id"] = operation_id
    return outbox.enqueue(event_type="support_operation", aggregate_type="support_ticket",
        aggregate_id=operation_id, idempotency_key="support-operation:" + operation_id,
        payload=payload)


def public_snapshot(repository, remote, identity, selected_id=None, *, page=0, page_info=None):
    """Refresh authenticated public data; network failure leaves cache readable."""
    offline = False
    if remote is not None:
        try:
            if page_info is not None:
                listing = remote.call("list", body=str(page * 100))
                page_info.update(ids=[t["ticket_id"] for t in listing["tickets"]],
                                 has_more=listing["tickets_truncated"])
                for ticket in listing["tickets"]:
                    repository.cache_public_ticket(identity.tenant_id, identity.installation_id,
                        ticket["ticket_id"], ticket, preserve_messages=True)
                if not selected_id:
                    return tuple(listing["tickets"]), False
            result = remote.call("detail" if selected_id else "list", ticket_id=selected_id or "")
            for ticket in result["tickets"]:
                repository.cache_public_ticket(identity.tenant_id, identity.installation_id,
                                               ticket["ticket_id"], ticket,
                                               preserve_messages=selected_id is None)
        except (SyncRemoteError, ControlCenterError, OSError):
            offline = True
        cached = repository.list_cached_public_tickets(identity.tenant_id, identity.installation_id,
                                                       limit=100,offset=page * 100)
        if page_info is not None:
            page_info.setdefault("ids",[t["ticket_id"] for t in cached])
            page_info.setdefault("has_more",len(cached) >= 100)
        if selected_id and not any(t["ticket_id"] == selected_id for t in cached):
            selected = repository.get_cached_public_ticket(identity.tenant_id, identity.installation_id, selected_id)
            if selected:
                cached = (*cached, selected)
        return cached, offline
    return (), offline


def cached_ticket(value, identity):
    return SupportTicket(id=value["ticket_id"], protocol=value["protocol"],
        tenant_id=identity.tenant_id, installation_id=identity.installation_id,
        subject=value["subject"], description=value["description"], category=value["category"],
        priority=value["priority"], status=value["status"],
        created_at=datetime.fromisoformat(value["opened_at"].replace("Z", "+00:00")),
        updated_at=datetime.fromisoformat(value["updated_at"].replace("Z", "+00:00")))
