"""Stateless, server-only Supabase repository for the production Control Center.

Only this backend receives the Supabase service-role credential. Browser code
never receives it, and failures intentionally omit upstream response bodies so
credentials or row contents cannot be reflected through the web application.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
import secrets
import socket
import ssl
from typing import Any, Callable, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

from control_center.domain import (
    AdminResetAuthorization,
    ControlCenterConflictError,
    ControlCenterError,
    ControlCenterNotFoundError,
    ControlCenterValidationError,
    DashboardSummary,
    ErpInstallation,
    FingerprintSummary,
    HealthFilters,
    HealthSnapshot,
    Incident,
    IncidentFilters,
    IncidentNote,
    ObservabilityEvent,
    ObservabilityFilters,
    PlatformUser,
    QATestRun,
    RiskFilters,
    RiskSummary,
    SupportTicket,
    SyncReceipt,
    Tenant,
    TenantFilters,
    TenantOverview,
    TicketFilters,
    TicketHistoryEntry,
    TicketInternalNote,
    VersionSummary,
    new_id,
    utc_now,
)
from control_center.sanitization import sanitize_mapping, sanitize_text
from control_center.security import hash_password, verify_password


Transport = Callable[[str, str, Mapping[str, str], bytes | None, float], tuple[int, Mapping[str, str], bytes]]
_SAFE_FILTER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,199}$")
_USERNAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9@._+-]{2,179}$")
_RISK_RANK = {
    "normal": 0, "low": 1, "warning": 2, "medium": 2,
    "high": 3, "critical": 4,
}
_DUMMY_PASSWORD_HASH = hash_password(secrets.token_urlsafe(32))
_MAX_RESPONSE_BYTES = 2_000_000


def _utc(value: datetime | None = None) -> datetime:
    current = value or utc_now()
    return current.astimezone(timezone.utc) if current.tzinfo else current.replace(tzinfo=timezone.utc)


def _dt(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return _utc(parsed)


def _iso(value: datetime | None = None) -> str:
    return _utc(value).isoformat().replace("+00:00", "Z")


def _identifier(value: object, field: str) -> str:
    candidate = str(value or "").strip()
    if not _SAFE_FILTER.fullmatch(candidate):
        raise ControlCenterValidationError(f"{field} invalido.")
    return candidate


def _limit(value: object, *, maximum: int = 500) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise ControlCenterValidationError("Limite invalido.") from None
    if not 1 <= parsed <= maximum:
        raise ControlCenterValidationError("Limite invalido.")
    return parsed


def _offset(value: object) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise ControlCenterValidationError("Offset invalido.") from None
    if not 0 <= parsed <= 1_000_000:
        raise ControlCenterValidationError("Offset invalido.")
    return parsed


def _mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _sequence(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(item)[:500] for item in value[:100])


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


def _default_transport(
    method: str,
    url: str,
    headers: Mapping[str, str],
    body: bytes | None,
    timeout: float,
) -> tuple[int, Mapping[str, str], bytes]:
    request = Request(url, data=body, headers=dict(headers), method=method)
    context = ssl.create_default_context()
    if hasattr(ssl, "TLSVersion"):
        context.minimum_version = ssl.TLSVersion.TLSv1_2
    opener = build_opener(HTTPSHandler(context=context), _NoRedirect())
    try:
        with opener.open(request, timeout=timeout) as response:
            if str(response.geturl()) != url:
                raise ControlCenterError("Redirecionamento remoto recusado.")
            raw = response.read(_MAX_RESPONSE_BYTES + 1)
            if len(raw) > _MAX_RESPONSE_BYTES:
                raise ControlCenterError("A resposta remota excedeu o limite seguro.")
            return int(response.status), dict(response.headers.items()), raw
    except HTTPError as exc:
        # Deliberately discard the body. PostgREST details may include row data.
        exc.read(64_000)
        return int(exc.code), dict(exc.headers.items()), b""
    except (URLError, TimeoutError, socket.timeout, OSError) as exc:
        raise ControlCenterError("O armazenamento remoto esta indisponivel.") from exc


class SupabaseControlCenterRepository:
    """Control Center repository backed only by private PostgREST tables."""

    storage_name = "supabase"

    def __init__(
        self,
        url: str,
        service_role_key: str,
        *,
        timeout_seconds: float = 10.0,
        transport: Transport | None = None,
        initialize: bool = True,
    ):
        candidate = str(url or "").strip().rstrip("/")
        try:
            parsed = urlsplit(candidate)
            port = parsed.port
        except ValueError:
            raise ValueError("Origem Supabase invalida.") from None
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or port not in {None, 443}
        ):
            raise ValueError("Origem Supabase de producao deve usar HTTPS.")
        credential = str(service_role_key or "").strip()
        if len(credential) < 32 or credential.startswith(
            ("SUBSTITUA_", "sb_publishable_")
        ):
            raise ValueError("Credencial server-only Supabase invalida.")
        self._base_url = candidate
        self._service_role_key = credential
        self._timeout = max(2.0, min(float(timeout_seconds), 30.0))
        self._transport = transport or _default_transport
        if initialize:
            self.initialize()

    @property
    def database_path(self) -> None:
        return None

    def close(self) -> None:
        return None

    def _request(
        self,
        method: str,
        path: str,
        *,
        query: Mapping[str, object] | Sequence[tuple[str, object]] | None = None,
        payload: object | None = None,
        prefer: str | None = None,
        expected: tuple[int, ...] = (200,),
    ) -> tuple[Any, Mapping[str, str]]:
        if not path.startswith("/") or ".." in path:
            raise ControlCenterValidationError("Caminho remoto invalido.")
        pairs = list(query.items()) if isinstance(query, Mapping) else list(query or ())
        suffix = "?" + urlencode([(key, str(value)) for key, value in pairs]) if pairs else ""
        raw_body = None
        headers = {
            "Accept": "application/json",
            "apikey": self._service_role_key,
            "User-Agent": "NexPoint-Control-Center/1",
        }
        if not self._service_role_key.startswith("sb_secret_"):
            headers["Authorization"] = f"Bearer {self._service_role_key}"
        if payload is not None:
            raw_body = json.dumps(
                payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
            ).encode("utf-8")
            if len(raw_body) > 262_144:
                raise ControlCenterValidationError("Payload remoto excede o limite.")
            headers["Content-Type"] = "application/json"
        if prefer:
            headers["Prefer"] = prefer
        status, response_headers, raw = self._transport(
            method, f"{self._base_url}{path}{suffix}", headers, raw_body, self._timeout
        )
        if status not in expected:
            if status == 404:
                raise ControlCenterNotFoundError("Registro remoto nao encontrado.")
            if status in {409, 412}:
                raise ControlCenterConflictError("Conflito no armazenamento remoto.")
            if status in {400, 422}:
                raise ControlCenterValidationError("Operacao remota invalida.")
            raise ControlCenterError("O armazenamento remoto rejeitou a operacao.")
        if not raw:
            return None, response_headers
        content_type = next(
            (
                str(value).split(";", 1)[0].strip().casefold()
                for key, value in response_headers.items()
                if str(key).casefold() == "content-type"
            ),
            "",
        )
        if content_type != "application/json":
            raise ControlCenterError("Resposta remota possui tipo invalido.")
        try:
            return json.loads(raw), response_headers
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ControlCenterError("Resposta invalida do armazenamento remoto.") from exc

    def _select(
        self,
        table: str,
        *,
        filters: Sequence[tuple[str, object]] = (),
        select: str = "*",
        order: str | None = None,
        limit: int = 500,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        query: list[tuple[str, object]] = [("select", select)]
        query.extend(filters)
        if order:
            query.append(("order", order))
        query.extend((("limit", _limit(limit, maximum=1000)), ("offset", _offset(offset))))
        result, _headers = self._request("GET", f"/rest/v1/{table}", query=query)
        if not isinstance(result, list) or any(not isinstance(row, dict) for row in result):
            raise ControlCenterError("Resposta tabular invalida do armazenamento remoto.")
        return result

    def _insert(
        self,
        table: str,
        payload: Mapping[str, Any] | Sequence[Mapping[str, Any]],
        *,
        upsert: bool = False,
        on_conflict: str | None = None,
    ) -> list[dict[str, Any]]:
        query = (("on_conflict", on_conflict),) if on_conflict else ()
        prefer = "return=representation"
        if upsert:
            prefer += ",resolution=merge-duplicates"
        result, _headers = self._request(
            "POST",
            f"/rest/v1/{table}",
            query=query,
            payload=payload,
            prefer=prefer,
            expected=(200, 201),
        )
        if not isinstance(result, list) or any(not isinstance(row, dict) for row in result):
            raise ControlCenterError("Resposta de gravacao invalida.")
        return result

    def _patch(
        self, table: str, filters: Sequence[tuple[str, object]], payload: Mapping[str, Any]
    ) -> list[dict[str, Any]]:
        result, _headers = self._request(
            "PATCH",
            f"/rest/v1/{table}",
            query=filters,
            payload=payload,
            prefer="return=representation",
            expected=(200,),
        )
        if not isinstance(result, list):
            raise ControlCenterError("Resposta de atualizacao invalida.")
        return result

    def _delete(self, table: str, filters: Sequence[tuple[str, object]]) -> None:
        self._request(
            "DELETE",
            f"/rest/v1/{table}",
            query=filters,
            prefer="return=minimal",
            expected=(200, 204),
        )

    def _rpc(self, name: str, payload: Mapping[str, Any]) -> Any:
        result, _headers = self._request(
            "POST",
            f"/rest/v1/rpc/{name}",
            payload=payload,
            expected=(200,),
        )
        return result

    @staticmethod
    def _rpc_success(result: Any, operation: str) -> dict[str, Any]:
        row = result[0] if isinstance(result, list) and result else result
        if not isinstance(row, Mapping):
            raise ControlCenterError(f"Resposta remota invalida em {operation}.")
        if row.get("ok") is not True:
            code = str(row.get("code") or "remote_rejected")
            if code.endswith("_conflict") or code == "idempotency_conflict":
                raise ControlCenterConflictError(f"Conflito remoto em {operation}.")
            if code in {"installation_invalid", "not_found"}:
                raise ControlCenterNotFoundError(f"Registro remoto ausente em {operation}.")
            if code in {
                "invalid_payload", "environment_mismatch", "unauthorized",
                "recovery_request_invalid", "recovery_request_expired",
                "authorization_unavailable",
            }:
                raise ControlCenterValidationError(f"Operacao remota recusada em {operation}.")
            raise ControlCenterError(f"Operacao remota falhou em {operation}.")
        return dict(row)

    def initialize(self) -> None:
        # Probe every critical production surface so a partial migration never
        # reports the web process as ready.
        for table in (
            "np_platform_users", "np_tenants", "np_installations",
            "np_support_tickets", "np_health_snapshots", "np_risks",
            "np_incidents", "np_observability_events",
        ):
            self._select(table, select="id", limit=1)
        self._rpc("np_admin_get_reset_authorization", {"p_ticket_id": "readiness_probe"})

    @staticmethod
    def _tenant(row: Mapping[str, Any], installations: Sequence[Mapping[str, Any]] = (),
                health_rows: Sequence[Mapping[str, Any]] = ()) -> Tenant:
        tenant_id = str(row.get("id", ""))
        owned = [item for item in installations if str(item.get("tenant_id")) == tenant_id]
        latest = max(
            owned,
            key=lambda item: _dt(item.get("last_seen_at")) or datetime.min.replace(tzinfo=timezone.utc),
            default={},
        )
        latest_health = max(
            (item for item in health_rows if str(item.get("tenant_id")) == tenant_id),
            key=lambda item: _dt(item.get("reported_at")) or datetime.min.replace(tzinfo=timezone.utc),
            default={},
        )
        state = str(latest_health.get("app_state", "unknown"))
        health = {
            "healthy": "healthy", "normal": "normal", "warning": "warning",
            "high": "high", "critical": "critical", "degraded": "warning",
            "unavailable": "offline", "offline": "offline",
        }.get(state, "unknown")
        kind = str(row.get("kind", "customer"))
        return Tenant(
            id=tenant_id,
            display_name=sanitize_text(row.get("display_name"), maximum=160),
            status={"closed": "inactive"}.get(str(row.get("status")), str(row.get("status", "active"))),
            created_at=_dt(row.get("created_at")) or utc_now(),
            updated_at=_dt(row.get("updated_at")) or utc_now(),
            erp_version=str(latest.get("app_version") or "unknown")[:80],
            environment={"prod": "production"}.get(str(latest.get("environment")), str(latest.get("environment") or "unknown")),
            last_seen_at=_dt(latest.get("last_seen_at")),
            health_status=health,
            is_demo=False,
            tenant_type={
                "test": "TEST", "demo": "DEMO", "internal": "INTERNAL",
            }.get(kind, "CUSTOMER"),
        )

    @staticmethod
    def _installation(row: Mapping[str, Any], health_rows: Sequence[Mapping[str, Any]] = ()) -> ErpInstallation:
        installation_id = str(row.get("id", ""))
        latest = max(
            (item for item in health_rows if str(item.get("installation_id")) == installation_id),
            key=lambda item: _dt(item.get("reported_at")) or datetime.min.replace(tzinfo=timezone.utc),
            default={},
        )
        state = str(latest.get("app_state", "unknown"))
        health = {
            "healthy": "healthy", "normal": "normal", "warning": "warning",
            "high": "high", "critical": "critical", "degraded": "warning",
            "unavailable": "offline", "offline": "offline",
        }.get(state, "unknown")
        return ErpInstallation(
            id=installation_id,
            tenant_id=str(row.get("tenant_id", "")),
            installation_id=str(row.get("installation_key") or installation_id),
            version=str(row.get("app_version") or "unknown")[:80],
            build=str(row.get("build_id") or "unknown")[:80],
            environment={"prod": "production"}.get(str(row.get("environment")), str(row.get("environment") or "unknown")),
            last_seen_at=_dt(row.get("last_seen_at")),
            health=health,
            platform="windows",
            metadata={"label": sanitize_text(row.get("label"), maximum=160)},
            created_at=_dt(row.get("created_at")) or utc_now(),
            updated_at=_dt(row.get("updated_at")) or utc_now(),
        )

    @staticmethod
    def _health(row: Mapping[str, Any]) -> HealthSnapshot:
        metadata = _mapping(row.get("metadata"))
        state = str(row.get("app_state", "unknown"))
        return HealthSnapshot(
            id=str(row.get("id", "")),
            tenant_id=str(row.get("tenant_id", "")),
            installation_id=str(row.get("installation_id", "")),
            status={
                "healthy": "healthy", "normal": "normal", "warning": "warning",
                "high": "high", "critical": "critical", "degraded": "warning",
                "unavailable": "offline", "offline": "offline",
            }.get(state, "unknown"),
            risk_score=int(row.get("risk_score") or 0),
            recent_errors=int(row.get("recent_errors") or row.get("doctor_fail") or 0),
            retry_count=int(row.get("retry_count") or 0),
            latency_ms=int(row["latency_ms"]) if isinstance(row.get("latency_ms"), int) else None,
            fingerprints=_sequence(row.get("fingerprints")),
            captured_at=_dt(row.get("reported_at")) or utc_now(),
            details=sanitize_mapping({
                **metadata,
                "database_state": row.get("database_state"),
                "migration_state": row.get("migration_state"),
                "outbox_state": row.get("outbox_state"),
                "sync_state": row.get("sync_state"),
                "nexa_state": row.get("nexa_state"),
                "doctor_pass": row.get("doctor_pass"),
                "doctor_warn": row.get("doctor_warn"),
                "doctor_fail": row.get("doctor_fail"),
            }),
        )

    @staticmethod
    def _ticket_event_notes(rows: Sequence[Mapping[str, Any]], ticket_id: str) -> tuple[TicketInternalNote, ...]:
        notes = []
        for row in rows:
            if str(row.get("ticket_id")) != ticket_id or row.get("event_type") != "internal_note":
                continue
            metadata = _mapping(row.get("metadata"))
            notes.append(TicketInternalNote(
                id=str(row.get("id", "")),
                ticket_id=ticket_id,
                author_id=str(metadata.get("author_id") or "platform"),
                body=sanitize_text(metadata.get("body"), maximum=2000),
                created_at=_dt(row.get("occurred_at")) or utc_now(),
            ))
        return tuple(sorted(notes, key=lambda item: (item.created_at, item.id)))

    @staticmethod
    def _ticket_history(rows: Sequence[Mapping[str, Any]], ticket_id: str) -> tuple[TicketHistoryEntry, ...]:
        history = []
        for row in rows:
            if str(row.get("ticket_id")) != ticket_id:
                continue
            metadata = _mapping(row.get("metadata"))
            history.append(TicketHistoryEntry(
                id=str(row.get("id", "")),
                ticket_id=ticket_id,
                event_type=str(row.get("event_type") or "updated")[:100],
                from_status=str(metadata.get("from_status")) if metadata.get("from_status") else None,
                to_status=str(metadata.get("to_status")) if metadata.get("to_status") else None,
                actor_id=str(metadata.get("actor_id")) if metadata.get("actor_id") else None,
                detail=sanitize_text(metadata.get("detail"), maximum=500) or None,
                created_at=_dt(row.get("occurred_at")) or utc_now(),
            ))
        return tuple(sorted(history, key=lambda item: (item.created_at, item.id)))

    @classmethod
    def _ticket(cls, row: Mapping[str, Any], events: Sequence[Mapping[str, Any]] = ()) -> SupportTicket:
        metadata = _mapping(row.get("metadata"))
        ticket_id = str(row.get("id", ""))
        status = {"cancelled": "closed"}.get(str(row.get("status")), str(row.get("status") or "open"))
        return SupportTicket(
            id=ticket_id,
            protocol=str(metadata.get("protocol") or row.get("idempotency_key") or ticket_id)[:128],
            tenant_id=str(row.get("tenant_id", "")),
            installation_id=str(row.get("installation_id")) if row.get("installation_id") else None,
            created_by=str(metadata.get("created_by") or "installation")[:128],
            subject=sanitize_text(row.get("subject"), maximum=240),
            category=str(row.get("category") or "general")[:64],
            description=sanitize_text(row.get("sanitized_description"), maximum=4000),
            status=status,
            priority=str(row.get("priority") or metadata.get("priority") or "normal"),
            created_at=_dt(row.get("opened_at")) or _dt(row.get("created_at")) or utc_now(),
            updated_at=_dt(row.get("updated_at")) or utc_now(),
            module=sanitize_text(metadata.get("module"), maximum=64) or None,
            screen=sanitize_text(metadata.get("screen"), maximum=128) or None,
            erp_version=sanitize_text(metadata.get("erp_version"), maximum=80) or None,
            technical_context=sanitize_mapping(metadata.get("technical_context") if isinstance(metadata.get("technical_context"), Mapping) else {}),
            diagnostic_fingerprint=str(metadata.get("diagnostic_fingerprint")) if metadata.get("diagnostic_fingerprint") else None,
            correlation_id=str(row.get("correlation_id")) if row.get("correlation_id") else None,
            risk_id=str(metadata.get("risk_id")) if metadata.get("risk_id") else None,
            nexa_diagnosis=sanitize_text(metadata.get("nexa_diagnosis"), maximum=4000) or None,
            resolved_at=_dt(metadata.get("resolved_at")),
            closed_at=_dt(row.get("closed_at")),
            internal_notes=cls._ticket_event_notes(events, ticket_id),
            history=cls._ticket_history(events, ticket_id),
        )

    @staticmethod
    def _risk(row: Mapping[str, Any]) -> RiskSummary:
        severity = str(row.get("severity") or "low")
        status = {"acknowledged": "monitoring", "closed": "resolved"}.get(str(row.get("status")), str(row.get("status") or "open"))
        return RiskSummary(
            id=str(row.get("id", "")), tenant_id=str(row.get("tenant_id", "")),
            installation_id=str(row.get("installation_id")) if row.get("installation_id") else None,
            module=str(row.get("module") or "unknown")[:64],
            fingerprint=str(row.get("fingerprint") or ""), score=int(row.get("score") or 0),
            level=severity, confidence=str(row.get("confidence") or "medium"),
            evidence=_sequence(row.get("evidence")),
            probable_cause=sanitize_text(row.get("probable_cause"), maximum=500) or None,
            first_seen_at=_dt(row.get("detected_at")) or utc_now(),
            last_seen_at=_dt(row.get("updated_at")) or utc_now(), status=status,
        )

    @staticmethod
    def _incident(row: Mapping[str, Any]) -> Incident:
        summary = _mapping(row.get("summary"))
        notes = tuple(
            IncidentNote(
                id=str(item.get("id") or new_id("incident_note")),
                incident_id=str(row.get("id", "")),
                author_id=str(item.get("author_id") or "platform"),
                body=sanitize_text(item.get("body"), maximum=2000),
                created_at=_dt(item.get("created_at")) or utc_now(),
            )
            for item in summary.get("notes", ())[:100]
            if isinstance(item, Mapping)
        )
        severity = {"low": "normal", "medium": "warning"}.get(str(row.get("severity")), str(row.get("severity") or "warning"))
        return Incident(
            id=str(row.get("id", "")), tenant_id=str(row.get("tenant_id", "")),
            title=sanitize_text(row.get("title"), maximum=240), status=str(row.get("status") or "open"),
            severity=severity, fingerprint=str(row.get("fingerprint")) if row.get("fingerprint") else None,
            ticket_id=str(summary.get("ticket_id")) if summary.get("ticket_id") else None,
            risk_id=str(summary.get("risk_id")) if summary.get("risk_id") else None,
            created_at=_dt(row.get("opened_at")) or _dt(row.get("created_at")) or utc_now(),
            updated_at=_dt(row.get("updated_at")) or utc_now(), resolved_at=_dt(row.get("resolved_at")), notes=notes,
        )

    @staticmethod
    def _event(row: Mapping[str, Any]) -> ObservabilityEvent:
        metadata = _mapping(row.get("metadata"))
        return ObservabilityEvent(
            event_id=str(row.get("id", "")), timestamp=_dt(row.get("occurred_at")) or utc_now(),
            level=str(row.get("level") or "info").upper(), environment=str(metadata.get("environment") or "production"),
            tenant_id=str(row.get("tenant_id", "")), installation_id=str(row.get("installation_id", "")),
            user_pseudonym=str(metadata.get("user_pseudonym")) if metadata.get("user_pseudonym") else None,
            session_id=str(metadata.get("session_id")) if metadata.get("session_id") else None,
            correlation_id=str(row.get("correlation_id")) if row.get("correlation_id") else None,
            request_id=str(metadata.get("request_id")) if metadata.get("request_id") else None,
            module=str(metadata.get("module") or row.get("component") or "erp"), component=str(row.get("component") or "application"),
            event_type=str(row.get("event_name") or "diagnostic.event"), operation=str(metadata.get("operation") or "unknown"),
            status=str(metadata.get("status") or "observed"), duration_ms=metadata.get("duration_ms") if isinstance(metadata.get("duration_ms"), int) else None,
            error_code=str(metadata.get("error_code")) if metadata.get("error_code") else None,
            fingerprint=str(row.get("fingerprint")) if row.get("fingerprint") else None,
            retry_count=int(metadata.get("retry_count") or 0), metadata=sanitize_mapping(metadata),
            schema_version=int(metadata.get("schema_version") or 1), app_version=str(metadata.get("app_version")) if metadata.get("app_version") else None,
            build=str(metadata.get("build")) if metadata.get("build") else None,
        )

    @staticmethod
    def _platform_user(row: Mapping[str, Any], scopes: Sequence[Mapping[str, Any]] = ()) -> PlatformUser:
        user_id = str(row.get("id", ""))
        return PlatformUser(
            id=user_id, username=str(row.get("username") or "").casefold(),
            display_name=sanitize_text(row.get("display_name"), maximum=160), role=str(row.get("role") or ""),
            password_hash=str(row.get("password_hash") or ""), active=bool(row.get("active")),
            created_at=_dt(row.get("created_at")) or utc_now(), updated_at=_dt(row.get("updated_at")) or utc_now(),
            last_login_at=_dt(row.get("last_login_at")),
            authorized_tenant_ids=tuple(str(item.get("tenant_id")) for item in scopes if str(item.get("user_id")) == user_id),
        )

    def _fleet(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        return (
            self._select("np_tenants", order="created_at.desc", limit=1000),
            self._select("np_installations", order="created_at.desc", limit=1000),
            self._select("np_health_snapshots", order="reported_at.desc", limit=1000),
        )

    def get_tenant(self, tenant_id: str) -> Tenant | None:
        target = _identifier(tenant_id, "tenant_id")
        rows = self._select("np_tenants", filters=(("id", f"eq.{target}"),), limit=1)
        if not rows:
            return None
        installations = self._select("np_installations", filters=(("tenant_id", f"eq.{target}"),), limit=500)
        health = self._select("np_health_snapshots", filters=(("tenant_id", f"eq.{target}"),), order="reported_at.desc", limit=500)
        return self._tenant(rows[0], installations, health)

    def list_tenants(self, filters: TenantFilters | None = None, *, status: str | None = None,
                     health_status: str | None = None, query: str | None = None,
                     limit: int = 200, offset: int = 0) -> tuple[Tenant, ...]:
        tenants, installations, health = self._fleet()
        selected = filters or TenantFilters(status=status, health_status=health_status, query=query)
        result = [self._tenant(row, installations, health) for row in tenants]
        if selected.status:
            result = [item for item in result if item.status == selected.status]
        if selected.health_status:
            result = [item for item in result if item.health_status == selected.health_status]
        if selected.query:
            needle = sanitize_text(selected.query, maximum=120).casefold()
            result = [item for item in result if needle in item.display_name.casefold()]
        return tuple(result[_offset(offset):_offset(offset) + _limit(limit)])

    def list_installations(self, *, tenant_id: str | None = None, health: str | None = None,
                           limit: int = 200, offset: int = 0) -> tuple[ErpInstallation, ...]:
        filters = (("tenant_id", f"eq.{_identifier(tenant_id, 'tenant_id')}"),) if tenant_id else ()
        rows = self._select("np_installations", filters=filters, order="last_seen_at.desc.nullslast", limit=_limit(limit), offset=_offset(offset))
        health_rows = self._select("np_health_snapshots", filters=filters, order="reported_at.desc", limit=1000)
        result = [self._installation(row, health_rows) for row in rows]
        if health:
            result = [item for item in result if item.health == health]
        return tuple(result)

    def get_installation(self, installation_id: str) -> ErpInstallation | None:
        target = _identifier(installation_id, "installation_id")
        rows = self._select("np_installations", filters=(("id", f"eq.{target}"),), limit=1)
        if not rows:
            return None
        health = self._select("np_health_snapshots", filters=(("installation_id", f"eq.{target}"),), order="reported_at.desc", limit=1)
        return self._installation(rows[0], health)

    def list_health_snapshots(self, filters: HealthFilters | None = None, *, latest_only: bool = False,
                              limit: int = 200) -> tuple[HealthSnapshot, ...]:
        selected = filters or HealthFilters()
        clauses = []
        if selected.tenant_id:
            clauses.append(("tenant_id", f"eq.{_identifier(selected.tenant_id, 'tenant_id')}"))
        if selected.installation_id:
            clauses.append(("installation_id", f"eq.{_identifier(selected.installation_id, 'installation_id')}"))
        rows = self._select("np_health_snapshots", filters=tuple(clauses), order="reported_at.desc", limit=_limit(limit, maximum=1000))
        values = [self._health(row) for row in rows]
        if selected.statuses:
            values = [item for item in values if item.status in selected.statuses]
        if latest_only:
            unique: dict[str, HealthSnapshot] = {}
            for item in values:
                unique.setdefault(item.installation_id, item)
            values = list(unique.values())
        return tuple(values)

    def _ticket_events(self, ticket_id: str | None = None) -> list[dict[str, Any]]:
        filters = (("ticket_id", f"eq.{_identifier(ticket_id, 'ticket_id')}"),) if ticket_id else ()
        return self._select("np_support_ticket_events", filters=filters, order="occurred_at.asc", limit=1000)

    def get_ticket(self, ticket_id: str) -> SupportTicket | None:
        target = _identifier(ticket_id, "ticket_id")
        rows = self._select("np_support_tickets", filters=(("id", f"eq.{target}"),), limit=1)
        return self._ticket(rows[0], self._ticket_events(target)) if rows else None

    def get_tenant_ticket(self, tenant_id: str, ticket_id: str) -> SupportTicket | None:
        ticket = self.get_ticket(ticket_id)
        return ticket if ticket and ticket.tenant_id == _identifier(tenant_id, "tenant_id") else None

    def list_tickets(self, filters: TicketFilters | None = None, *, status: str | None = None,
                     tenant_id: str | None = None, priority: str | None = None,
                     category: str | None = None, limit: int = 200,
                     offset: int = 0) -> tuple[SupportTicket, ...]:
        selected = filters or TicketFilters(
            statuses=(status,) if status else (), tenant_id=tenant_id,
            priorities=(priority,) if priority else (), categories=(category,) if category else (),
        )
        clauses = []
        if selected.tenant_id:
            clauses.append(("tenant_id", f"eq.{_identifier(selected.tenant_id, 'tenant_id')}"))
        rows = self._select("np_support_tickets", filters=tuple(clauses), order="updated_at.desc", limit=_limit(limit), offset=_offset(offset))
        events = self._ticket_events() if rows else []
        values = [self._ticket(row, events) for row in rows]
        if selected.statuses:
            values = [item for item in values if item.status in selected.statuses]
        if selected.priorities:
            values = [item for item in values if item.priority in selected.priorities]
        if selected.categories:
            values = [item for item in values if item.category in selected.categories]
        if selected.query:
            needle = sanitize_text(selected.query, maximum=120).casefold()
            values = [item for item in values if needle in f"{item.protocol} {item.subject}".casefold()]
        return tuple(values)

    def list_tenant_tickets(self, tenant_id: str, *, statuses: Sequence[str] | None = None,
                            limit: int = 100, offset: int = 0) -> tuple[SupportTicket, ...]:
        return self.list_tickets(TicketFilters(tenant_id=tenant_id, statuses=tuple(statuses or ())), limit=limit, offset=offset)

    def list_ticket_history(self, ticket_id: str) -> tuple[TicketHistoryEntry, ...]:
        return self._ticket_history(self._ticket_events(ticket_id), _identifier(ticket_id, "ticket_id"))

    def list_tenant_ticket_history(self, tenant_id: str, ticket_id: str) -> tuple[TicketHistoryEntry, ...]:
        if self.get_tenant_ticket(tenant_id, ticket_id) is None:
            return ()
        return self.list_ticket_history(ticket_id)

    def set_ticket_status(self, ticket_id: str, status: str, *, changed_by: str | None = None,
                          changed_at: datetime | None = None) -> SupportTicket:
        ticket = self.get_ticket(ticket_id)
        if ticket is None:
            raise ControlCenterNotFoundError("Chamado nao encontrado.")
        target = str(status).strip().casefold()
        cloud_target = "closed" if target == "closed" else target
        now = _utc(changed_at)
        rows = self._patch("np_support_tickets", (("id", f"eq.{ticket.id}"),), {
            "status": cloud_target, "closed_at": _iso(now) if target == "closed" else None,
        })
        if not rows:
            raise ControlCenterConflictError("Chamado nao foi atualizado.")
        self._insert("np_support_ticket_events", {
            "tenant_id": ticket.tenant_id, "installation_id": ticket.installation_id,
            "ticket_id": ticket.id, "idempotency_key": f"cc_status_{secrets.token_hex(16)}",
            "event_type": "status_changed", "occurred_at": _iso(now),
            "metadata": sanitize_mapping({"from_status": ticket.status, "to_status": target, "actor_id": changed_by}),
        })
        return self.get_ticket(ticket.id) or self._ticket(rows[0])

    def add_ticket_internal_note(self, ticket_id: str, body: str, author_id: str, *,
                                 created_at: datetime | None = None) -> TicketInternalNote:
        ticket = self.get_ticket(ticket_id)
        if ticket is None or not ticket.installation_id:
            raise ControlCenterNotFoundError("Chamado nao encontrado.")
        clean_body = sanitize_text(body, maximum=2000)
        if not clean_body:
            raise ControlCenterValidationError("Nota interna vazia.")
        rows = self._insert("np_support_ticket_events", {
            "tenant_id": ticket.tenant_id, "installation_id": ticket.installation_id,
            "ticket_id": ticket.id, "idempotency_key": f"cc_note_{secrets.token_hex(16)}",
            "event_type": "internal_note", "occurred_at": _iso(created_at),
            "metadata": {"author_id": _identifier(author_id, "author_id"), "body": clean_body},
        })
        return self._ticket_event_notes(rows, ticket.id)[0]

    def list_risk_summaries(self, filters: RiskFilters | None = None, *, tenant_id: str | None = None,
                            level: str | None = None, status: str | None = None,
                            limit: int = 200, offset: int = 0) -> tuple[RiskSummary, ...]:
        selected = filters or RiskFilters(tenant_id=tenant_id, levels=(level,) if level else (), statuses=(status,) if status else ())
        clauses = []
        if selected.tenant_id:
            clauses.append(("tenant_id", f"eq.{_identifier(selected.tenant_id, 'tenant_id')}"))
        rows = self._select("np_risks", filters=tuple(clauses), order="updated_at.desc", limit=_limit(limit), offset=_offset(offset))
        values = [self._risk(row) for row in rows]
        if selected.levels:
            values = [item for item in values if item.level in selected.levels]
        if selected.statuses:
            values = [item for item in values if item.status in selected.statuses]
        if selected.module:
            values = [item for item in values if item.module == selected.module]
        return tuple(values)

    def get_risk_summary(self, risk_id: str) -> RiskSummary | None:
        rows = self._select("np_risks", filters=(("id", f"eq.{_identifier(risk_id, 'risk_id')}"),), limit=1)
        return self._risk(rows[0]) if rows else None

    def list_incidents(self, filters: IncidentFilters | None = None, *, tenant_id: str | None = None,
                       status: str | None = None, severity: str | None = None,
                       limit: int = 200, offset: int = 0) -> tuple[Incident, ...]:
        selected = filters or IncidentFilters(tenant_id=tenant_id, statuses=(status,) if status else (), severities=(severity,) if severity else ())
        clauses = []
        if selected.tenant_id:
            clauses.append(("tenant_id", f"eq.{_identifier(selected.tenant_id, 'tenant_id')}"))
        rows = self._select("np_incidents", filters=tuple(clauses), order="updated_at.desc", limit=_limit(limit), offset=_offset(offset))
        values = [self._incident(row) for row in rows]
        if selected.statuses:
            values = [item for item in values if item.status in selected.statuses]
        if selected.severities:
            values = [item for item in values if item.severity in selected.severities]
        return tuple(values)

    def get_incident(self, incident_id: str) -> Incident | None:
        rows = self._select("np_incidents", filters=(("id", f"eq.{_identifier(incident_id, 'incident_id')}"),), limit=1)
        return self._incident(rows[0]) if rows else None

    def _installation_for_tenant(self, tenant_id: str) -> ErpInstallation:
        rows = self.list_installations(tenant_id=tenant_id, limit=1)
        if not rows:
            raise ControlCenterValidationError("Empresa sem instalacao ativa.")
        return rows[0]

    def create_incident(self, incident: Incident) -> Incident:
        tenant = self.get_tenant(incident.tenant_id)
        if tenant is None:
            raise ControlCenterNotFoundError("Empresa nao encontrada.")
        installation = None
        if incident.ticket_id:
            source_ticket = self.get_ticket(incident.ticket_id)
            if source_ticket is None or source_ticket.tenant_id != tenant.id:
                raise ControlCenterValidationError("Chamado fora do escopo da empresa.")
            if source_ticket.installation_id:
                installation = self.get_installation(source_ticket.installation_id)
        if installation is None and incident.risk_id:
            source_risk = self.get_risk_summary(incident.risk_id)
            if source_risk is None or source_risk.tenant_id != tenant.id:
                raise ControlCenterValidationError("Risco fora do escopo da empresa.")
            if source_risk.installation_id:
                installation = self.get_installation(source_risk.installation_id)
        installation = installation or self._installation_for_tenant(tenant.id)
        if installation.tenant_id != tenant.id:
            raise ControlCenterValidationError("Instalacao fora do escopo da empresa.")
        severity = {"normal": "low", "warning": "medium"}.get(incident.severity, incident.severity)
        correlation = f"cc_{secrets.token_hex(16)}"
        rows = self._insert("np_incidents", {
            "tenant_id": tenant.id, "installation_id": installation.id,
            "idempotency_key": f"cc_incident_{secrets.token_hex(16)}", "correlation_id": correlation,
            "source_incident_id": _identifier(incident.id, "incident_id"),
            "fingerprint": incident.fingerprint, "status": incident.status, "severity": severity,
            "title": sanitize_text(incident.title, maximum=240), "opened_at": _iso(incident.created_at),
            "resolved_at": _iso(incident.resolved_at) if incident.resolved_at else None,
            "summary": sanitize_mapping({"ticket_id": incident.ticket_id, "risk_id": incident.risk_id, "notes": []}),
        })
        return self._incident(rows[0])

    def create_incident_from_ticket(self, ticket_id: str, *, title: str | None = None,
                                    severity: str = "warning") -> Incident:
        ticket = self.get_ticket(ticket_id)
        if ticket is None:
            raise ControlCenterNotFoundError("Chamado nao encontrado.")
        return self.create_incident(Incident(
            tenant_id=ticket.tenant_id, title=title or f"Incidente do chamado {ticket.protocol}: {ticket.subject}",
            severity=severity, fingerprint=ticket.diagnostic_fingerprint, ticket_id=ticket.id, risk_id=ticket.risk_id,
        ))

    def create_incident_from_risk(self, risk_id: str, *, title: str | None = None,
                                  severity: str | None = None) -> Incident:
        risk = self.get_risk_summary(risk_id)
        if risk is None:
            raise ControlCenterNotFoundError("Risco nao encontrado.")
        mapped = {
            "low": "normal", "warning": "warning", "medium": "warning",
            "high": "high", "critical": "critical", "normal": "normal",
        }[risk.level]
        return self.create_incident(Incident(
            tenant_id=risk.tenant_id, title=title or f"Incidente do risco {risk.fingerprint}",
            severity=severity or mapped, fingerprint=risk.fingerprint, risk_id=risk.id,
        ))

    def set_incident_status(self, incident_id: str, status: str, *, changed_at: datetime | None = None) -> Incident:
        target = str(status).strip().casefold()
        rows = self._patch("np_incidents", (("id", f"eq.{_identifier(incident_id, 'incident_id')}"),), {
            "status": target, "resolved_at": _iso(changed_at) if target == "resolved" else None,
        })
        if not rows:
            raise ControlCenterNotFoundError("Incidente nao encontrado.")
        return self._incident(rows[0])

    def add_incident_note(self, incident_id: str, body: str, author_id: str, *,
                          created_at: datetime | None = None) -> IncidentNote:
        incident = self.get_incident(incident_id)
        if incident is None:
            raise ControlCenterNotFoundError("Incidente nao encontrado.")
        note = IncidentNote(
            id=new_id("incident_note"), incident_id=incident.id,
            author_id=_identifier(author_id, "author_id"), body=sanitize_text(body, maximum=2000),
            created_at=_utc(created_at),
        )
        if not note.body:
            raise ControlCenterValidationError("Nota vazia.")
        summary = {"ticket_id": incident.ticket_id, "risk_id": incident.risk_id, "notes": [asdict(item) for item in (*incident.notes, note)]}
        self._patch("np_incidents", (("id", f"eq.{incident.id}"),), {"summary": sanitize_mapping(summary)})
        return note

    def list_observability_events(self, filters: ObservabilityFilters | None = None, *,
                                  limit: int = 200, offset: int = 0) -> tuple[ObservabilityEvent, ...]:
        selected = filters or ObservabilityFilters()
        if selected.tenant_ids is not None and not selected.tenant_ids:
            return ()
        clauses = []
        if selected.tenant_id:
            clauses.append(("tenant_id", f"eq.{_identifier(selected.tenant_id, 'tenant_id')}"))
        if selected.installation_id:
            clauses.append(("installation_id", f"eq.{_identifier(selected.installation_id, 'installation_id')}"))
        if selected.correlation_id:
            clauses.append(("correlation_id", f"eq.{_identifier(selected.correlation_id, 'correlation_id')}"))
        if selected.fingerprint:
            clauses.append(("fingerprint", f"eq.{_identifier(selected.fingerprint, 'fingerprint')}"))
        rows = self._select("np_observability_events", filters=tuple(clauses), order="occurred_at.desc", limit=_limit(limit, maximum=1000), offset=_offset(offset))
        values = [self._event(row) for row in rows]
        if selected.tenant_ids is not None:
            allowed = set(selected.tenant_ids)
            values = [item for item in values if item.tenant_id in allowed]
        if selected.started_at:
            values = [item for item in values if item.timestamp >= _utc(selected.started_at)]
        if selected.ended_at:
            values = [item for item in values if item.timestamp <= _utc(selected.ended_at)]
        for attr, expected in (("level", selected.levels),):
            if expected:
                values = [item for item in values if getattr(item, attr) in expected]
        for attr in ("module", "component", "event_type", "status", "error_code", "operation"):
            expected = getattr(selected, attr)
            if expected:
                values = [item for item in values if getattr(item, attr) == expected]
        if selected.query:
            needle = sanitize_text(selected.query, maximum=120).casefold()
            values = [item for item in values if needle in f"{item.module} {item.component} {item.event_type} {item.error_code or ''}".casefold()]
        return tuple(values)

    def get_observability_event(self, event_id: str) -> ObservabilityEvent | None:
        rows = self._select("np_observability_events", filters=(("id", f"eq.{_identifier(event_id, 'event_id')}"),), limit=1)
        return self._event(rows[0]) if rows else None

    def get_observability_timeline(self, correlation_id: str, *, tenant_id: str | None = None,
                                   limit: int = 500) -> tuple[ObservabilityEvent, ...]:
        return tuple(reversed(self.list_observability_events(
            ObservabilityFilters(tenant_id=tenant_id, correlation_id=correlation_id), limit=limit
        )))

    def record_observability_event(self, event: ObservabilityEvent) -> ObservabilityEvent:
        metadata = sanitize_mapping({
            **dict(event.metadata), "environment": event.environment,
            "user_pseudonym": event.user_pseudonym, "request_id": event.request_id,
            "module": event.module, "operation": event.operation, "status": event.status,
            "duration_ms": event.duration_ms, "error_code": event.error_code,
            "retry_count": event.retry_count, "schema_version": event.schema_version,
            "app_version": event.app_version, "build": event.build,
        })
        correlation = event.correlation_id or f"cc_{secrets.token_hex(16)}"
        rows = self._insert("np_observability_events", {
            "tenant_id": _identifier(event.tenant_id, "tenant_id"),
            "installation_id": _identifier(event.installation_id, "installation_id"),
            "idempotency_key": f"cc_event_{secrets.token_hex(16)}", "correlation_id": _identifier(correlation, "correlation_id"),
            "event_name": _identifier(event.event_type, "event_type"), "level": event.level.casefold(),
            "component": _identifier(event.component, "component"), "occurred_at": _iso(event.timestamp),
            "fingerprint": event.fingerprint, "metadata": metadata,
        })
        return self._event(rows[0])

    def list_fingerprint_summaries(self, *, tenant_id: str | None = None,
                                   tenant_ids: Sequence[str] | None = None,
                                   limit: int = 200) -> tuple[FingerprintSummary, ...]:
        events = self.list_observability_events(
            ObservabilityFilters(tenant_id=tenant_id, tenant_ids=tuple(tenant_ids) if tenant_ids is not None else None),
            limit=1000,
        )
        grouped: dict[str, list[ObservabilityEvent]] = defaultdict(list)
        for event in events:
            if event.fingerprint:
                grouped[event.fingerprint].append(event)
        summaries = [FingerprintSummary(
            fingerprint=fingerprint, occurrence_count=len(items),
            first_seen_at=min(item.timestamp for item in items), last_seen_at=max(item.timestamp for item in items),
            affected_tenants=len({item.tenant_id for item in items}),
            module=max(items, key=lambda item: item.timestamp).module,
            latest_level=max(items, key=lambda item: item.timestamp).level,
        ) for fingerprint, items in grouped.items()]
        summaries.sort(key=lambda item: (item.last_seen_at, item.fingerprint), reverse=True)
        return tuple(summaries[:_limit(limit)])

    def get_fingerprint_summary(self, fingerprint: str, *, tenant_id: str | None = None,
                                tenant_ids: Sequence[str] | None = None) -> FingerprintSummary | None:
        target = _identifier(fingerprint, "fingerprint")
        return next((item for item in self.list_fingerprint_summaries(tenant_id=tenant_id, tenant_ids=tenant_ids, limit=500) if item.fingerprint == target), None)

    def export_observability_diagnostics(self, event_id: str) -> Mapping[str, Any]:
        event = self.get_observability_event(event_id)
        if event is None:
            raise ControlCenterNotFoundError("Evento nao encontrado.")
        timeline = self.get_observability_timeline(event.correlation_id or event.event_id, tenant_id=event.tenant_id, limit=50)
        return sanitize_mapping({"schema_version": 1, "event": asdict(event), "timeline": [asdict(item) for item in timeline]})

    def save_platform_user(self, user: PlatformUser, *, password: str | None = None) -> PlatformUser:
        username = str(user.username or "").strip().casefold()
        if not _USERNAME.fullmatch(username) or user.role not in {"platform_admin", "nexpoint_control_admin"}:
            raise ControlCenterValidationError("Usuario de plataforma invalido.")
        if password is not None and len(password) < 12:
            raise ControlCenterValidationError("A senha da plataforma deve possuir pelo menos 12 caracteres.")
        existing = self.get_platform_user_by_username(username)
        password_hash = hash_password(password) if password is not None else (existing.password_hash if existing else user.password_hash)
        if not password_hash:
            raise ControlCenterValidationError("Senha da plataforma ausente.")
        row = {
            "id": existing.id if existing else None,
            "username": username, "display_name": sanitize_text(user.display_name, maximum=160),
            "role": user.role, "password_hash": password_hash, "active": bool(user.active),
        }
        if row["id"] is None:
            row.pop("id")
        rows = self._insert("np_platform_users", row, upsert=True, on_conflict="username")
        saved_id = str(rows[0]["id"])
        self._delete("np_platform_user_tenants", (("user_id", f"eq.{saved_id}"),))
        if user.role == "nexpoint_control_admin" and user.authorized_tenant_ids:
            self._insert("np_platform_user_tenants", [
                {"user_id": saved_id, "tenant_id": _identifier(item, "tenant_id")}
                for item in dict.fromkeys(user.authorized_tenant_ids)
            ])
        return self.get_platform_user(saved_id) or self._platform_user(rows[0])

    def _platform_scopes(self, user_id: str | None = None) -> list[dict[str, Any]]:
        filters = (("user_id", f"eq.{_identifier(user_id, 'user_id')}"),) if user_id else ()
        return self._select("np_platform_user_tenants", filters=filters, limit=1000)

    def get_platform_user(self, user_id: str) -> PlatformUser | None:
        target = _identifier(user_id, "user_id")
        rows = self._select("np_platform_users", filters=(("id", f"eq.{target}"),), limit=1)
        return self._platform_user(rows[0], self._platform_scopes(target)) if rows else None

    def get_platform_user_by_username(self, username: str) -> PlatformUser | None:
        normalized = str(username or "").strip().casefold()
        if not _USERNAME.fullmatch(normalized):
            return None
        rows = self._select("np_platform_users", filters=(("username", f"eq.{normalized}"),), limit=1)
        return self._platform_user(rows[0], self._platform_scopes(str(rows[0]["id"]))) if rows else None

    def list_platform_users(self) -> tuple[PlatformUser, ...]:
        rows = self._select("np_platform_users", order="username.asc", limit=500)
        scopes = self._platform_scopes()
        return tuple(self._platform_user(row, scopes) for row in rows)

    def authenticate_platform_user(self, username: str, password: str) -> PlatformUser | None:
        user = self.get_platform_user_by_username(username)
        encoded = user.password_hash if user is not None else _DUMMY_PASSWORD_HASH
        valid = verify_password(password, encoded)
        if user is None or not valid or not user.active:
            return None
        rows = self._patch("np_platform_users", (("id", f"eq.{user.id}"),), {"last_login_at": _iso()})
        return self._platform_user(rows[0], self._platform_scopes(user.id)) if rows else None

    @staticmethod
    def platform_user_can_access_tenant(user: PlatformUser, tenant_id: str) -> bool:
        return user.active and (
            user.role == "platform_admin"
            or (user.role == "nexpoint_control_admin" and tenant_id in user.authorized_tenant_ids)
        )

    def list_tenant_overviews(self, filters: TenantFilters | None = None, *, limit: int = 200,
                              offset: int = 0) -> tuple[TenantOverview, ...]:
        tenants = self.list_tenants(filters, limit=limit, offset=offset)
        tickets = self.list_tickets(limit=500)
        risks = self.list_risk_summaries(limit=500)
        installations = self.list_installations(limit=500)
        return tuple(TenantOverview(
            tenant=tenant,
            installation_count=sum(item.tenant_id == tenant.id for item in installations),
            open_ticket_count=sum(item.tenant_id == tenant.id and item.status not in {"resolved", "closed"} for item in tickets),
            current_risk_level=max(
                (item.level for item in risks if item.tenant_id == tenant.id and item.status in {"open", "monitoring"}),
                key=lambda item: _RISK_RANK.get(item, 0), default="normal",
            ),
        ) for tenant in tenants)

    def dashboard_summary(self, *, now: datetime | None = None,
                          recent_window: timedelta = timedelta(minutes=15)) -> DashboardSummary:
        if recent_window <= timedelta(0) or recent_window > timedelta(days=30):
            raise ControlCenterValidationError("Janela de atividade invalida.")
        instant = _utc(now)
        tenants = self.list_tenants(limit=500)
        installations = self.list_installations(limit=500)
        tickets = self.list_tickets(limit=500)
        risks = self.list_risk_summaries(limit=500)
        incidents = self.list_incidents(limit=500)
        return DashboardSummary(
            total_tenants=len(tenants), active_tenants=sum(item.status == "active" for item in tenants),
            inactive_tenants=sum(item.status in {"inactive", "suspended"} for item in tenants),
            recent_installations=sum(item.last_seen_at is not None and item.last_seen_at >= instant - recent_window for item in installations),
            stale_installations=sum(item.last_seen_at is None or item.last_seen_at < instant - recent_window for item in installations),
            different_versions=len({item.version for item in installations}),
            open_tickets=sum(item.status == "open" for item in tickets),
            in_progress_tickets=sum(item.status == "in_progress" for item in tickets),
            waiting_customer_tickets=sum(item.status == "waiting_customer" for item in tickets),
            high_risks=sum(item.level == "high" and item.status in {"open", "monitoring"} for item in risks),
            critical_risks=sum(item.level == "critical" and item.status in {"open", "monitoring"} for item in risks),
            recent_incidents=sum(item.created_at >= instant - timedelta(days=7) for item in incidents),
            commercial_tenants=sum(item.tenant_type == "CUSTOMER" for item in tenants),
            commercial_active_tenants=sum(item.tenant_type == "CUSTOMER" and item.status == "active" for item in tenants),
            commercial_inactive_tenants=sum(item.tenant_type == "CUSTOMER" and item.status != "active" for item in tenants),
            test_tenants=sum(item.tenant_type == "TEST" for item in tenants), demo_tenants=0,
        )

    def version_summaries(self, *, tenant_ids: Sequence[str] | None = None) -> tuple[VersionSummary, ...]:
        if tenant_ids is not None and not tenant_ids:
            return ()
        allowed = set(tenant_ids or ())
        installations = [item for item in self.list_installations(limit=500) if tenant_ids is None or item.tenant_id in allowed]
        risks = self.list_risk_summaries(limit=500)
        result = []
        for version, count in Counter(item.version for item in installations).most_common():
            members = [item for item in installations if item.version == version]
            result.append(VersionSummary(
                version=version, installation_count=count,
                healthy_count=sum(item.health in {"healthy", "normal"} for item in members),
                warning_count=sum(item.health not in {"healthy", "normal"} for item in members),
                high_risk_count=sum(any(risk.installation_id == item.id and risk.level in {"high", "critical"} and risk.status in {"open", "monitoring"} for risk in risks) for item in members),
            ))
        return tuple(result)

    # ERP ingestion is owned by the authenticated Edge Function. These methods
    # intentionally refuse to bypass that boundary from the web process.
    def apply_sync_envelope(self, envelope: object) -> SyncReceipt:
        raise ControlCenterValidationError("Ingestao direta e proibida em producao.")

    def resolve_erp_identity(self, *args: Any, **kwargs: Any) -> tuple[str, str]:
        raise ControlCenterValidationError("Resolucao local de identidade e proibida em producao.")

    def upsert_tenant(self, tenant: Tenant) -> Tenant:
        raise ControlCenterValidationError("Use o fluxo administrativo de provisionamento.")

    def upsert_installation(self, installation: ErpInstallation) -> ErpInstallation:
        raise ControlCenterValidationError("Use o fluxo administrativo de provisionamento.")

    def record_health_snapshot(self, snapshot: HealthSnapshot) -> HealthSnapshot:
        raise ControlCenterValidationError("Health deve entrar pelo endpoint autenticado.")

    def create_ticket(self, ticket: SupportTicket) -> SupportTicket:
        raise ControlCenterValidationError("Chamados devem entrar pelo endpoint autenticado.")

    def create_ticket_for_tenant(self, tenant_id: str, ticket: SupportTicket) -> SupportTicket:
        raise ControlCenterValidationError("Chamados devem entrar pelo endpoint autenticado.")

    def upsert_risk_summary(self, risk: RiskSummary) -> RiskSummary:
        raise ControlCenterValidationError("Riscos devem entrar pelo endpoint autenticado.")

    def reconcile_installation_risks(self, tenant_id: str, installation_id: str,
                                     active_fingerprints: tuple[str, ...]) -> int:
        raise ControlCenterValidationError("Reconciliacao deve entrar pelo endpoint autenticado.")

    def create_qa_test_run(self, run: QATestRun) -> QATestRun:
        raise ControlCenterValidationError("QA e proibido no Control Center PROD.")

    def get_qa_test_run(self, run_id: str) -> QATestRun | None:
        return None

    def list_qa_test_runs(self, tenant_id: str, *, limit: int = 100) -> tuple[QATestRun, ...]:
        return ()

    def finish_qa_test_run(self, run_id: str, *, status: str, observed_result: str,
                           finished_at: datetime | None = None) -> QATestRun:
        raise ControlCenterValidationError("QA e proibido no Control Center PROD.")

    def reset_test_tenant(self, tenant_id: str) -> Mapping[str, int]:
        raise ControlCenterValidationError("Reset QA e proibido no Control Center PROD.")

    def authorize_admin_reset(self, ticket_id: str, *, authorized_by: str,
                               lifetime_minutes: int = 15) -> AdminResetAuthorization:
        result = self._rpc("np_admin_authorize_reset", {
            "p_ticket_id": _identifier(ticket_id, "ticket_id"),
            "p_authorized_by": _identifier(authorized_by, "authorized_by"),
            "p_lifetime_minutes": int(lifetime_minutes),
        })
        row = self._rpc_success(result, "autorizar recuperacao")
        return AdminResetAuthorization(
            id=str(row.get("authorization_id", "")), ticket_id=str(row.get("ticket_id", ticket_id)),
            tenant_id=str(row.get("tenant_id", "")), installation_id=str(row.get("installation_id", "")),
            status=str(row.get("status") or "active"), expires_at=_dt(row.get("expires_at")) or utc_now(),
            authorized_by=authorized_by, created_at=_dt(row.get("created_at")) or utc_now(),
            used_at=_dt(row.get("used_at")),
        )

    def get_admin_reset_authorization(self, ticket_id: str) -> AdminResetAuthorization | None:
        # No verifier/digest is ever exposed through this projection.
        result = self._rpc("np_admin_get_reset_authorization", {"p_ticket_id": _identifier(ticket_id, "ticket_id")})
        row = result[0] if isinstance(result, list) and result else result
        if not isinstance(row, Mapping) or not row:
            return None
        return AdminResetAuthorization(
            id=str(row.get("id", "")), ticket_id=str(row.get("ticket_id", ticket_id)),
            tenant_id=str(row.get("tenant_id", "")), installation_id=str(row.get("installation_id", "")),
            status=str(row.get("status") or "active"), expires_at=_dt(row.get("expires_at")) or utc_now(),
            authorized_by=str(row.get("authorized_by") or "platform"), created_at=_dt(row.get("created_at")) or utc_now(),
            used_at=_dt(row.get("used_at")),
        )

    def find_available_admin_reset_authorization(self, *, tenant_id: str, installation_id: str,
                                                 requester_ref: str) -> AdminResetAuthorization | None:
        raise ControlCenterValidationError("Consumo de reset pertence ao endpoint ERP autenticado.")

    def consume_available_admin_reset_authorization(self, *, tenant_id: str, installation_id: str,
                                                    requester_ref: str) -> AdminResetAuthorization:
        raise ControlCenterValidationError("Consumo de reset pertence ao endpoint ERP autenticado.")

    def provision_installation(self, *, tenant_key: str, display_name: str, tenant_kind: str,
                               installation_key: str, label: str, environment: str,
                               key_id: str, secret_digest_hex: str) -> Mapping[str, Any]:
        normalized_environment = str(environment).casefold()
        if normalized_environment == "production":
            normalized_environment = "prod"
        result = self._rpc("np_admin_provision_installation", {
            "p_tenant_key": _identifier(tenant_key, "tenant_key"),
            "p_display_name": sanitize_text(display_name, maximum=160),
            "p_kind": str(tenant_kind).casefold(),
            "p_tenant_status": "active",
            "p_installation_key": _identifier(installation_key, "installation_key"),
            "p_label": sanitize_text(label, maximum=160),
            "p_environment": normalized_environment,
            "p_channel": normalized_environment,
            "p_key_id": _identifier(key_id, "key_id"), "p_secret_digest_hex": secret_digest_hex,
        })
        return self._rpc_success(result, "provisionar instalacao")

    def rotate_installation_credential(self, *, installation_id: str, key_id: str,
                                       secret_digest_hex: str) -> Mapping[str, Any]:
        result = self._rpc("np_admin_rotate_installation_credential", {
            "p_installation_id": _identifier(installation_id, "installation_id"),
            "p_key_id": _identifier(key_id, "key_id"), "p_secret_digest_hex": secret_digest_hex,
        })
        return self._rpc_success(result, "rotacionar credencial")

    def revoke_installation_credential(self, *, installation_id: str, key_id: str) -> None:
        result = self._rpc("np_admin_revoke_installation_credential", {
            "p_installation_id": _identifier(installation_id, "installation_id"),
            "p_key_id": _identifier(key_id, "key_id"),
        })
        self._rpc_success(result, "revogar credencial")
