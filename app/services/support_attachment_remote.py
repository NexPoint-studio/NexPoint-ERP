"""Authenticated screenshot transfer, separate from public conversation projections."""
import base64
from datetime import datetime
import re

from app.services.support_remote import SupabaseSupportRemote, SupportRemoteError, UUID, IDENTIFIER
from control_center.attachments import validate_attachment, MAX_ATTACHMENT_BYTES
from control_center.domain import ControlCenterError, TicketAttachment


class SupabaseSupportAttachmentRemote(SupabaseSupportRemote):
    function_name = "erp-support-attachments"
    request_domain = "nexpoint-erp-support-attachments-v1"
    response_domain = "nexpoint-erp-support-attachments-response-v1"

    def upload(self, ticket_id, attachment):
        validate_attachment(attachment.name, attachment.content_type, attachment.data)
        return self.transfer("upload", ticket_id, attachment.id,
                             name=attachment.name, content_type=attachment.content_type,
                             sha256=attachment.sha256,
                             data_base64=base64.b64encode(attachment.data).decode("ascii"))

    def transfer(self, action, ticket_id, attachment_id="", *, name="", content_type="", sha256="", data_base64=""):
        if (action not in {"upload", "download", "list"} or not IDENTIFIER.fullmatch(ticket_id)
                or (action != "list" and not UUID.fullmatch(attachment_id))):
            raise SupportRemoteError("Identificação de anexo inválida.", retryable=False)
        result = self._exchange(dict(schema_version=1, tenant_id=self.credentials.tenant_id,
            installation_id=self.credentials.installation_id, action=action, ticket_id=ticket_id,
            attachment_id=attachment_id, name=name, content_type=content_type,
            sha256=sha256, data_base64=data_base64))
        try:
            if set(result) != {"schema_version", "ok", "request_id", "attachments"}:
                raise ValueError()
            values = result["attachments"]
            if not isinstance(values, list) or len(values) > 3:
                raise ValueError()
            attachments = []
            for item in values:
                if set(item) != {"id", "name", "content_type", "sha256", "size_bytes", "data_base64", "created_at"}:
                    raise ValueError()
                if (not UUID.fullmatch(item["id"]) or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"])
                        or type(item["size_bytes"]) is not int or not 0 < item["size_bytes"] <= MAX_ATTACHMENT_BYTES
                        or not isinstance(item["name"], str) or len(item["name"]) > 120
                        or item["content_type"] not in {"image/png", "image/jpeg", "image/webp"}):
                    raise ValueError()
                data = base64.b64decode(item["data_base64"], validate=True)
                if action == "download":
                    _, _, _, digest = validate_attachment(item["name"], item["content_type"], data)
                    if digest != item["sha256"] or len(data) != item["size_bytes"] or item["id"] != attachment_id:
                        raise ValueError()
                elif data:
                    raise ValueError()
                attachments.append(TicketAttachment(id=item["id"], ticket_id=ticket_id,
                    name=item["name"], content_type=item["content_type"], sha256=item["sha256"],
                    size_bytes=item["size_bytes"], data=data,
                    created_at=datetime.fromisoformat(item["created_at"].replace("Z", "+00:00"))))
            if action != "list" and (len(attachments) != 1 or attachments[0].id != attachment_id):
                raise ValueError()
            return tuple(attachments)
        except (ValueError, KeyError, TypeError, ControlCenterError):
            raise SupportRemoteError("Resposta de anexo inválida.") from None
