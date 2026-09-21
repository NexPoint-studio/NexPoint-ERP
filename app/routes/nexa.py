"""Same-origin ERP UI proxy to the authenticated Nexa server bridge."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import ssl
import socket
import time
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import (
    HTTPRedirectHandler,
    HTTPSHandler,
    Request as UrlRequest,
    build_opener,
    urlopen,
)
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from app.services.nexa_adapter import build_nexa_snapshot, safe_public_web_query
from app.observability.context import emit_observability_event


router = APIRouter(prefix="/nexa")
MAX_MESSAGE = 4000
MAX_HISTORY = 4
HISTORY_TTL = 3600
_HISTORY: dict[tuple[int, str], tuple[float, list[dict[str, str]]]] = {}
_DIAGNOSES: dict[tuple[int, str], tuple[float, str, str]] = {}
_SCREEN_PATH = re.compile(r"^/[a-z0-9/_-]{1,120}$")
_AVAILABILITY_CACHE: tuple[float, str, str] = (0.0, "", "offline")
_LOCAL_SOURCE_SUFFIXES = (
    ".localhost",
    ".local",
    ".localdomain",
    ".internal",
    ".home",
    ".lan",
)
NEXA_INSTALLATION_REQUEST_SIGNATURE_DOMAIN = "nexa-erp-installation-v1"
NEXA_INSTALLATION_RESPONSE_SIGNATURE_DOMAIN = "nexa-erp-installation-response-v1"
_OPAQUE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{7,127}$")


def _bridge_url(*, production: bool = False) -> str | None:
    raw = os.getenv(
        "NEXA_ERP_BRIDGE_URL",
        "" if production else "http://127.0.0.1:54421/functions/v1/erp-chat",
    ).strip()
    parsed = urlsplit(raw)
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        return None
    if parsed.scheme == "https" and parsed.hostname:
        return raw
    if (
        not production
        and parsed.scheme == "http"
        and parsed.hostname in {"127.0.0.1", "localhost"}
    ):
        return raw
    return None


def _bridge_availability(secret: str, configured_url: str | None = None) -> str:
    """Reporta disponibilidade local real sem executar a função ou enviar dados."""
    global _AVAILABILITY_CACHE
    url = configured_url or _bridge_url()
    if len(secret) < 32 or url is None:
        return "unconfigured"
    parsed = urlsplit(url)
    # O painel de contexto não inicia tráfego externo. Uma URL HTTPS válida fica
    # configurada, e a chamada autenticada continua sendo a prova de conexão.
    if parsed.hostname not in {"127.0.0.1", "localhost"}:
        return "configured"
    now = time.monotonic()
    cached_at, cached_url, cached_state = _AVAILABILITY_CACHE
    if cached_url == url and now - cached_at < 2.0:
        return cached_state
    port = parsed.port or 80
    try:
        with socket.create_connection((parsed.hostname, port), timeout=0.08):
            state = "available"
    except OSError:
        state = "offline"
    _AVAILABILITY_CACHE = (now, url, state)
    return state


def _public_https_source(raw: object) -> str | None:
    """Accept only browser links that cannot name an obvious local network."""

    if not isinstance(raw, str) or not 0 < len(raw) <= 2048:
        return None
    try:
        parsed = urlsplit(raw)
        hostname = (parsed.hostname or "").rstrip(".").casefold()
        # Accessing ``port`` also rejects malformed or out-of-range ports.
        _port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or hostname == "localhost"
        or hostname.endswith(_LOCAL_SOURCE_SUFFIXES)
    ):
        return None
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        # Reject single-label and legacy numeric IP spellings such as 127.1 or
        # 0x7f000001, which browsers may normalize to loopback addresses.
        if "." not in hostname or re.fullmatch(r"[0-9a-f.x]+", hostname):
            return None
        labels = hostname.split(".")
        if any(
            not label
            or len(label) > 63
            or label.startswith("-")
            or label.endswith("-")
            or re.fullmatch(r"[a-z0-9-]+", label) is None
            for label in labels
        ):
            return None
    else:
        if not address.is_global:
            return None
    return raw


def _safe_sources(raw_sources: object) -> list[dict[str, str]]:
    if not isinstance(raw_sources, list):
        return []
    safe: list[dict[str, str]] = []
    for item in raw_sources[:20]:
        if not isinstance(item, dict):
            continue
        url = _public_https_source(item.get("url"))
        if url is None:
            continue
        title = item.get("title")
        safe.append({
            "url": url,
            "title": str(title).strip()[:200] if isinstance(title, str) else "",
        })
        if len(safe) == 10:
            break
    return safe


def _response_signature_hex(
    secret: str,
    timestamp: str,
    nonce: str,
    request_id: str,
    raw_body: bytes,
    *,
    tenant_id: str | None = None,
    installation_id: str | None = None,
) -> str:
    if tenant_id is None and installation_id is None:
        prefix = f"nexa-erp-response-v1\n{timestamp}\n{nonce}\n{request_id}\n"
    elif (
        isinstance(tenant_id, str)
        and isinstance(installation_id, str)
        and _OPAQUE_ID.fullmatch(tenant_id)
        and _OPAQUE_ID.fullmatch(installation_id)
    ):
        prefix = (
            f"{NEXA_INSTALLATION_RESPONSE_SIGNATURE_DOMAIN}\n{tenant_id}\n"
            f"{installation_id}\n{timestamp}\n{nonce}\n{request_id}\n"
        )
    else:
        raise ValueError("Escopo de instalacao Nexa invalido")
    signed_response = prefix.encode("utf-8") + raw_body
    return hmac.new(
        secret.encode("utf-8"), signed_response, hashlib.sha256
    ).hexdigest()


class _RejectNexaRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        del req, fp, code, msg, headers, newurl
        return None


def _open_production_nexa(request: UrlRequest, timeout: float):
    context = ssl.create_default_context()
    if hasattr(ssl, "TLSVersion"):
        context.minimum_version = ssl.TLSVersion.TLSv1_2
    return build_opener(
        HTTPSHandler(context=context), _RejectNexaRedirects()
    ).open(request, timeout=timeout)


def _send_signed(
    url: str,
    secret: str,
    payload: dict,
    request_id: str,
    *,
    tenant_id: str | None = None,
    installation_id: str | None = None,
    caller: str | None = None,
    production_opener=None,
) -> dict:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    timestamp = str(int(time.time()))
    production = tenant_id is not None or installation_id is not None
    if production:
        if (
            not isinstance(tenant_id, str)
            or not isinstance(installation_id, str)
            or not _OPAQUE_ID.fullmatch(tenant_id)
            or not _OPAQUE_ID.fullmatch(installation_id)
        ):
            raise ValueError("Escopo de instalacao Nexa invalido")
        nonce = secrets.token_hex(32)
        body_hash = hashlib.sha256(body).hexdigest()
        signed = (
            f"{NEXA_INSTALLATION_REQUEST_SIGNATURE_DOMAIN}\n{tenant_id}\n"
            f"{installation_id}\n{timestamp}\n{nonce}\n{body_hash}"
        ).encode("utf-8")
    else:
        nonce = str(uuid4())
        signed = timestamp.encode() + b"\n" + nonce.encode() + b"\n" + body
    signature = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    headers = {
        "Content-Type": "application/json",
        "X-ERP-Timestamp": timestamp,
        "X-ERP-Nonce": nonce,
        "X-ERP-Signature": f"v1={signature}" if production else signature,
        "X-Request-ID": request_id,
    }
    if production:
        headers.update({
            "Authorization": f"Bearer {secret}",
            "X-Nexpoint-Tenant-Id": tenant_id,
            "X-Nexpoint-Installation-Id": installation_id,
        })
    elif caller is not None:
        if caller != "control-center":
            raise ValueError("Origem Nexa invalida")
        headers["X-Nexpoint-Caller"] = caller
    request = UrlRequest(url, body, headers, method="POST")
    opener = (production_opener or _open_production_nexa) if production else urlopen
    with opener(request, timeout=12) as response:
        if response.status != 200:
            raise URLError("Nexa indisponível")
        raw = response.read(65537)
        response_request_id = response.headers.get("X-Request-ID", "")
        response_signature = response.headers.get("X-Nexa-Signature", "")
    if len(raw) > 65536:
        raise ValueError("Resposta da Nexa excedeu o limite")
    if response_request_id != request_id:
        raise ValueError("Resposta da Nexa não corresponde à solicitação")
    match = re.fullmatch(r"v1=([0-9a-f]{64})", response_signature.strip())
    if match is None:
        raise ValueError("Resposta da Nexa sem assinatura válida")
    expected_response_signature = _response_signature_hex(
        secret,
        timestamp,
        nonce,
        request_id,
        raw,
        tenant_id=tenant_id,
        installation_id=installation_id,
    )
    if not hmac.compare_digest(match.group(1), expected_response_signature):
        raise ValueError("Assinatura da resposta Nexa inválida")
    result = json.loads(raw)
    if (
        not isinstance(result, dict)
        or result.get("ok") is not True
        or result.get("request_id") != request_id
    ):
        raise ValueError("Resposta da Nexa inválida")
    data = result.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("reply"), str):
        raise ValueError("Resposta da Nexa inválida")
    return data


def _history(request: Request) -> tuple[tuple[int, str], list[dict[str, str]]]:
    thread = request.session.get("nexa_thread")
    if not isinstance(thread, str) or len(thread) != 32:
        thread = secrets.token_hex(16)
        request.session["nexa_thread"] = thread
    key = (request.state.current_user.id, thread)
    now = time.monotonic()
    if len(_HISTORY) > 1024:
        for old_key, (last_seen, _) in list(_HISTORY.items()):
            if now - last_seen > HISTORY_TTL:
                _HISTORY.pop(old_key, None)
        while len(_HISTORY) > 1024:
            _HISTORY.pop(next(iter(_HISTORY)))
    last_seen, messages = _HISTORY.get(key, (now, []))
    return key, list(messages) if now - last_seen < HISTORY_TTL else []


def resolve_assistant_diagnosis(
    request: Request, request_id: object, screen: object
) -> str | None:
    """Resolve an opaque, expiring reply reference for this actor and screen."""

    reference = str(request_id or "").strip()
    expected_screen = str(screen or "").strip()
    if not re.fullmatch(r"[0-9a-f-]{36}", reference) or not _SCREEN_PATH.fullmatch(expected_screen):
        return None
    key = (request.state.current_user.id, reference)
    stored = _DIAGNOSES.get(key)
    if stored is None:
        return None
    created_at, stored_screen, reply = stored
    if time.monotonic() - created_at > HISTORY_TTL or stored_screen != expected_screen:
        _DIAGNOSES.pop(key, None)
        return None
    return reply[:4000]


def consume_assistant_diagnosis(request: Request, request_id: object) -> None:
    reference = str(request_id or "").strip()
    if re.fullmatch(r"[0-9a-f-]{36}", reference):
        _DIAGNOSES.pop((request.state.current_user.id, reference), None)


@router.get("/context")
def context(request: Request, screen: str = ""):
    if request.state.current_user is None:
        raise HTTPException(status_code=401)
    _, safe_context, tools = build_nexa_snapshot(request, screen)
    risks = tools["get_module_health"].get("risks", [])
    new_alerts = []
    monitor = getattr(request.app.state, "diagnostic_monitor", None)
    if monitor is not None:
        try:
            all_visible = request.state.current_user.can("admin.audit.view")
            new_alerts = [
                ({"fingerprint": finding.fingerprint, "level": finding.severity}
                 if all_visible else {"level": finding.severity})
                for finding in monitor.alertable_findings(
                    actor_id=request.state.current_user.id, include_all=all_visible,
                    module=safe_context["module"],
                )
            ][:3]
        except Exception:
            pass
    availability = _bridge_availability(
        str(getattr(request.app.state, "nexa_secret", "")),
        str(getattr(request.app.state, "nexa_url", "")) or None,
    )
    return {
        "module": safe_context["module"],
        "screen": safe_context["screen"],
        "risks": risks,
        "new_alerts": new_alerts,
        "available": availability == "available",
        "availability": availability,
    }


@router.post("/chat")
async def chat(request: Request):
    if request.state.current_user is None:
        raise HTTPException(status_code=401)
    secret = getattr(request.app.state, "nexa_secret", "")
    settings = request.app.state.settings
    production = settings.environment == "production"
    url = (
        str(getattr(request.app.state, "nexa_url", ""))
        if production
        else _bridge_url()
    )
    tenant_id = (
        str(getattr(request.app.state, "nexa_tenant_id", "")) if production else None
    )
    installation_id = (
        str(getattr(request.app.state, "nexa_installation_id", ""))
        if production else None
    )
    if len(secret) < 32 or url is None:
        emit_observability_event(
            module="nexa", component="erp_bridge",
            event_type="nexa.unavailable", operation="chat",
            status="unconfigured", user_id=request.state.current_user.id,
            severity="WARNING", error_code="nexa_unconfigured",
            sync_required=True,
        )
        return JSONResponse({"error": "A Nexa ainda não está configurada neste ERP."}, status_code=503)
    try:
        body = await request.json()
    except (ValueError, UnicodeError):
        raise HTTPException(status_code=422) from None
    if not isinstance(body, dict) or set(body) != {"message", "screen"}:
        raise HTTPException(status_code=422)
    message = body["message"]
    if not isinstance(message, str) or not 0 < len(message.strip()) <= MAX_MESSAGE:
        raise HTTPException(status_code=422)
    if not isinstance(body["screen"], str) or not _SCREEN_PATH.fullmatch(body["screen"]):
        raise HTTPException(status_code=422)
    user_ref, safe_context, tools = build_nexa_snapshot(request, body["screen"], query=message)
    key, history = _history(request)
    payload = {
        "app": "erp",
        "message": message.strip(),
        "user_id": user_ref,
        "context": safe_context,
        "history": history[-MAX_HISTORY:],
        "tools": tools,
    }
    web_query = safe_public_web_query(message)
    if web_query:
        safe_context["web_query"] = web_query
    request_id = str(uuid4())
    started = time.monotonic()
    emit_observability_event(
        module="nexa", component="erp_bridge",
        event_type="nexa.request.started", operation="chat",
        status="started", user_id=request.state.current_user.id,
        request_id=request_id, sync_required=True,
    )
    try:
        data = await asyncio.to_thread(
            _send_signed,
            url,
            secret,
            payload,
            request_id,
            tenant_id=tenant_id,
            installation_id=installation_id,
        )
    except (HTTPError, URLError, OSError, ValueError, TimeoutError) as exc:
        emit_observability_event(
            module="nexa", component="erp_bridge",
            event_type="nexa.request.failed", operation="chat",
            status="failed", user_id=request.state.current_user.id,
            request_id=request_id,
            duration_ms=int((time.monotonic() - started) * 1000),
            severity="WARNING", error_code="nexa_unavailable",
            metadata={"exception_type": type(exc).__name__},
            sync_required=True,
        )
        emit_observability_event(
            module="nexa", component="erp_bridge",
            event_type=("nexa.timeout" if isinstance(exc, TimeoutError)
                        else "nexa.unavailable"),
            operation="chat", status="failed",
            user_id=request.state.current_user.id, request_id=request_id,
            duration_ms=int((time.monotonic() - started) * 1000),
            severity="WARNING", error_code="nexa_unavailable",
            metadata={"exception_type": type(exc).__name__},
            sync_required=True,
        )
        return JSONResponse({"error": "A Nexa está temporariamente indisponível. O ERP continua funcionando."}, status_code=503)
    emit_observability_event(
        module="nexa", component="erp_bridge",
        event_type="nexa.request.completed", operation="chat",
        status="completed", user_id=request.state.current_user.id,
        request_id=request_id,
        duration_ms=int((time.monotonic() - started) * 1000),
        metadata={
            "provider": data.get("provider") if isinstance(data.get("provider"), str) else None,
            "model": data.get("model") if isinstance(data.get("model"), str) else None,
        },
        sync_required=True,
    )
    used_tools = data.get("tools_used")
    if isinstance(used_tools, list):
        for tool in used_tools[:10]:
            if isinstance(tool, str):
                emit_observability_event(
                    module="nexa", component="erp_bridge",
                    event_type="nexa.tool.used", operation="tool",
                    status="completed", user_id=request.state.current_user.id,
                    request_id=request_id, metadata={"tool": tool},
                    sync_required=False,
                )
    reply = data["reply"][:8000]
    _HISTORY[key] = (time.monotonic(), (history + [
        {"role": "user", "content": message.strip()[:1000]},
        {"role": "assistant", "content": reply[:1000]},
    ])[-MAX_HISTORY:])
    if len(_DIAGNOSES) >= 1024:
        cutoff = time.monotonic() - HISTORY_TTL
        for old_key, (created_at, _screen, _reply) in tuple(_DIAGNOSES.items()):
            if created_at < cutoff:
                _DIAGNOSES.pop(old_key, None)
        while len(_DIAGNOSES) >= 1024:
            _DIAGNOSES.pop(next(iter(_DIAGNOSES)))
    _DIAGNOSES[(request.state.current_user.id, request_id)] = (
        time.monotonic(), body["screen"], reply[:4000]
    )
    return {
        "reply": reply,
        "sources": _safe_sources(data.get("sources")),
        "request_id": request_id,
    }
