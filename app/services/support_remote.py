"""Bounded installation-authenticated public support channel; no admin secrets."""
from __future__ import annotations

import hmac
import json
import re
import time
from uuid import uuid4
from urllib.request import Request
from urllib.parse import urlsplit

from app.core import cloud_profile
from app.services.admin_recovery_remote import _signature, _header, _iso_datetime
from app.services.sync_remote import _default_https_open, SyncRemoteError
from control_center.domain import ControlCenterError

REQUEST_DOMAIN = "nexpoint-erp-support-v1"
RESPONSE_DOMAIN = "nexpoint-erp-support-response-v1"
MAX_RESPONSE = 2 * 1024 * 1024
IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


class SupportRemoteError(SyncRemoteError):
    pass


class SupabaseSupportRemote:
    function_name = "erp-support"
    request_domain = REQUEST_DOMAIN
    response_domain = RESPONSE_DOMAIN

    def __init__(self, endpoint, credentials, *, opener=None, clock=time.time, timeout_seconds=10):
        cloud_profile.require_qa_endpoint(endpoint, self.function_name)
        cloud_profile.require_qa_identity(credentials.tenant_id, credentials.installation_id)
        parsed = urlsplit(endpoint)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or parsed.query or parsed.fragment or parsed.path != "/functions/v1/" + self.function_name
                or (parsed.port not in (None, 443) and not cloud_profile.packaged_qa())):
            raise ValueError("Origem de suporte inválida.")
        if not 2 <= timeout_seconds <= 30:
            raise ValueError("Timeout inválido.")
        self.endpoint, self.credentials = endpoint, credentials
        self.opener, self.clock, self.timeout = opener or _default_https_open, clock, timeout_seconds

    def call(self, action, *, ticket_id="", operation_id="", body="", status=""):
        if action not in {"list", "detail", "history", "reply", "status"}:
            raise SupportRemoteError("Ação inválida.", retryable=False)
        if (action != "list" and not IDENTIFIER.fullmatch(ticket_id)) or (
            action in {"reply", "status"} and not UUID.fullmatch(operation_id)
        ):
            raise SupportRemoteError("Identificação inválida.", retryable=False)
        if (action == "reply" and not 1 <= len(body.strip()) <= 4000) or (
            action == "status" and status not in {"open", "resolved", "closed"}
        ):
            raise SupportRemoteError("Conteúdo inválido.", retryable=False)
        if action in {"list", "history"} and (body and (not re.fullmatch(r"0|[1-9][0-9]{0,5}", body) or int(body) > 100000)):
            raise SupportRemoteError("Página inválida.", retryable=False)
        credentials = self.credentials
        payload = dict(schema_version=1, tenant_id=credentials.tenant_id,
            installation_id=credentials.installation_id, action=action, ticket_id=ticket_id,
            operation_id=operation_id, body=body, status=status)
        result = self._exchange(payload)
        try:
            if (set(result) != {"schema_version", "ok", "request_id", "tickets", "tickets_truncated"}
                    or type(result["tickets_truncated"]) is not bool
                    or not isinstance(result["tickets"], list) or len(result["tickets"]) > 100):
                raise ValueError()
            if action != "list" and len(result["tickets"]) != 1:
                raise ValueError()
            for ticket in result["tickets"]:
                self._validate_ticket(ticket)
                if action != "list" and ticket["ticket_id"] != ticket_id:
                    raise ValueError()
            return result
        except (ValueError, KeyError, TypeError, ControlCenterError):
            raise SupportRemoteError("Contrato de suporte inválido.") from None

    def _exchange(self, payload):
        credentials = self.credentials
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        timestamp, nonce, request_id = str(int(self.clock())), str(uuid4()), str(uuid4())
        signature = _signature(credentials.secret, self.request_domain, credentials.tenant_id,
            credentials.installation_id, timestamp, nonce, request_id, body=encoded)
        request = Request(self.endpoint, data=encoded, method="POST", headers={
            "Content-Type": "application/json", "Accept": "application/json",
            "Authorization": f"Bearer {credentials.secret}",
            "X-Nexpoint-Tenant-Id": credentials.tenant_id,
            "X-Nexpoint-Installation-Id": credentials.installation_id,
            "X-Nexpoint-Timestamp": timestamp, "X-Nexpoint-Nonce": nonce,
            "X-Request-Id": request_id, "X-Nexpoint-Signature": f"v1={signature}",
        })
        try:
            with self.opener(request, self.timeout) as response:
                if response.getcode() != 200 or response.geturl() != self.endpoint:
                    raise SupportRemoteError("Suporte remoto indisponível.")
                headers = response.headers
                if _header(headers, "Content-Type").split(";", 1)[0].strip() != "application/json":
                    raise SupportRemoteError("Resposta de suporte inválida.")
                raw = response.read(MAX_RESPONSE + 1)
        except SupportRemoteError:
            raise
        except Exception:
            # Never expose upstream bodies, URLs, authorization or user content.
            raise SupportRemoteError("Não foi possível sincronizar o suporte.") from None
        if len(raw) > MAX_RESPONSE:
            raise SupportRemoteError("Resposta de suporte excede o limite.")
        response_time = _header(headers, "X-Nexpoint-Timestamp")
        response_nonce = _header(headers, "X-Nexpoint-Nonce")
        if (not re.fullmatch(r"\d{10}", response_time)
                or abs(self.clock() - int(response_time)) > 60
                or response_nonce != nonce or _header(headers, "X-Request-Id") != request_id):
            raise SupportRemoteError("Resposta fora da solicitação atual.")
        expected = _signature(credentials.secret, self.response_domain, credentials.tenant_id,
            credentials.installation_id, response_time, response_nonce, request_id, body=raw)
        if not hmac.compare_digest(_header(headers, "X-Nexpoint-Signature"), "v1=" + expected):
            raise SupportRemoteError("Assinatura do suporte inválida.")
        try:
            result = json.loads(raw)
            if (not isinstance(result, dict) or result["schema_version"] != 1
                    or result["ok"] is not True or result["request_id"] != request_id):
                raise ValueError()
            return result
        except (ValueError, KeyError, TypeError, ControlCenterError):
            raise SupportRemoteError("Contrato de suporte inválido.") from None

    @staticmethod
    def _validate_ticket(ticket):
        fields = {"ticket_id", "protocol", "subject", "description", "category", "priority",
                  "status", "opened_at", "updated_at", "messages", "messages_truncated"}
        if not isinstance(ticket, dict) or set(ticket) != fields:
            raise ValueError()
        if not IDENTIFIER.fullmatch(ticket["ticket_id"]) or type(ticket["messages_truncated"]) is not bool:
            raise ValueError()
        if ticket["status"] not in {"open", "in_progress", "waiting_customer", "resolved", "closed"}:
            raise ValueError()
        for field in ("protocol", "subject", "description", "category", "priority"):
            if not isinstance(ticket[field], str) or len(ticket[field]) > 4000:
                raise ValueError()
        for field in ("opened_at", "updated_at"):
            _iso_datetime(ticket[field], field)
        if not isinstance(ticket["messages"], list) or len(ticket["messages"]) > 200:
            raise ValueError()
        for message in ticket["messages"]:
            if not isinstance(message, dict) or set(message) != {"id", "event_type", "body", "status", "created_at"}:
                raise ValueError()
            if (not isinstance(message["id"], str) or not IDENTIFIER.fullmatch(message["id"])
                    or message["event_type"] not in {"public_reply", "client_reply", "public_status"}
                    or not isinstance(message["body"], str) or len(message["body"]) > 4000):
                raise ValueError()
            if message["status"] not in {None, "", "open", "in_progress", "waiting_customer", "resolved", "closed"}:
                raise ValueError()
            _iso_datetime(message["created_at"], "created_at")
