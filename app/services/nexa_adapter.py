"""Read-only, minimized ERP snapshot for the signed Nexa bridge."""
from __future__ import annotations

from hashlib import sha256
import hmac
from dataclasses import asdict
import re

from app.core.modules import MODULES
from app.knowledge import ERP_HELP
from app.services.authorization import active_actor_access, require_effective_actor_permission, ActorAuthorizationError
from app.services.admin_lock import AdminLockService
from app.repositories.sync import OutboxRepository
import json


_SCREEN = re.compile(r"^/[a-z0-9/-]{1,100}$")
_PUBLIC_WEB_TOPICS = {
    "sqlite": "sqlite database error",
    "sqlalchemy": "sqlalchemy documentation",
    "fastapi": "fastapi documentation",
    "python": "python documentation",
    "supabase": "supabase authentication error",
    "postgrest": "postgrest error",
}


def safe_public_web_query(message: str) -> str | None:
    """Return a fixed public topic only; never interpolate customer/user text."""
    words = set(re.findall(r"[a-z]+", str(message or "").casefold()))
    for topic, query in _PUBLIC_WEB_TOPICS.items():
        if topic in words:
            return query
    return None


def screen_context(raw_screen: object, permissions: frozenset[str]) -> tuple[str, str, str]:
    """Treat browser path as a hint; return only fixed module/screen/operation labels."""
    path = str(raw_screen or "")
    if not _SCREEN.fullmatch(path):
        return "erp", "unknown", "view"
    if any(path == tab.path and tab.permission not in permissions for module in MODULES for tab in module.tabs):
        return "erp", "unknown", "view"
    for module in MODULES:
        for tab in module.tabs:
            if path == tab.path and tab.permission in permissions:
                return module.id, tab.id, "view"
        if path.startswith(module.route.rsplit("/", 1)[0] + "/") and module.permission in permissions:
            return module.id, "detail", "view"
    return "erp", "unknown", "view"


def public_help(query: str, permissions: frozenset[str]) -> list[dict[str, str]]:
    terms = set(re.findall(r"[a-z]{4,}", str(query or "").casefold()[:200]))
    return [
        {"topic": title, "summary": summary}
        for key, title, summary, permission in ERP_HELP
        if permission in permissions and (not terms or terms & set(re.findall(r"[a-z]{4,}", (key + title + summary).casefold())))
    ][:5]


def pseudonymous_user_id(secret: str, user_id: int) -> str:
    return hmac.new(secret.encode("utf-8"), f"erp-user:{user_id}".encode(), sha256).hexdigest()


def build_nexa_snapshot(request, screen: object, *, query: str = "") -> tuple[str, dict, dict]:
    """Recheck active actor in DB; never trust browser IDs, roles, or permissions."""
    user = request.state.current_user
    admin_lock_status = None
    with request.app.state.session_factory() as session:
        access = active_actor_access(session, user.id)
        if access.role_codes == frozenset({"support"}):
            permitted = set()
            for permission in user.permissions:
                try:
                    require_effective_actor_permission(session, user.id, permission)
                    permitted.add(permission)
                except ActorAuthorizationError:
                    pass
            permissions = frozenset(permitted)
        else:
            permissions = frozenset(user.permissions) & access.permissions
        if "admin" in access.role_codes and "admin.overview.view" in permissions:
            lock_status = AdminLockService(session).status()
            support_status = "none"
            for item in OutboxRepository(session).list_unsynced(
                event_type="support_ticket", limit=100
            ):
                try:
                    payload = json.loads(item.payload_json)
                except (TypeError, ValueError):
                    continue
                if payload.get("category") == "admin_access_recovery":
                    support_status = "pending_sync"
                    break
            admin_lock_status = {
                "configured": lock_status.configured,
                "failed_attempt_count": lock_status.failed_attempt_count,
                "lockout_until": (
                    lock_status.lockout_until.isoformat()
                    if lock_status.lockout_until else None
                ),
                "last_recovery_event": (
                    lock_status.last_recovery_at.isoformat()
                    if lock_status.last_recovery_at else None
                ),
                "support_ticket_status": support_status,
                "capabilities": [
                    "explain_recovery_options",
                    "open_support_guidance",
                ],
            }
    module, screen_name, operation = screen_context(screen, permissions)
    settings = request.app.state.settings
    role = "admin" if "admin" in access.role_codes else "support" if "support" in access.role_codes else "user"
    public_permissions = sorted(permissions)[:80]
    context = {
        "module": module,
        "screen": screen_name,
        "operation": operation,
        "role": role,
        "environment": settings.environment,
        "version": settings.version[:40],
        "build": settings.build[:40],
        "permissions": public_permissions,
    }
    monitor = getattr(request.app.state, "diagnostic_monitor", None)
    events = []
    risks = []
    if monitor is not None:
        try:
            all_visible = "admin.audit.view" in permissions
            events = monitor.recent(actor_id=user.id, include_all=all_visible, limit=100)
            risks = monitor.risk_report(actor_id=user.id, include_all=all_visible).findings
        except Exception:
            pass
    safe_events = []
    event_fields = (
        "id", "timestamp", "module", "component", "event_type", "operation",
        "category", "severity", "status", "error_code", "fingerprint",
        "correlation_id", "request_id", "duration_ms", "retry_count",
    ) if "admin.audit.view" in permissions else (
        "timestamp", "module", "category", "severity"
    )
    prioritized_events = (
        [event for event in events if getattr(event, "severity", None) in {"ERROR", "CRITICAL"}]
        + [event for event in events if getattr(event, "severity", None) == "WARNING"]
    )
    for event in prioritized_events[:10]:
        value = event if isinstance(event, dict) else asdict(event)
        snapshot = {
            key: value[key].isoformat() if key == "timestamp" else value[key]
            for key in event_fields if key in value
        }
        if "id" in snapshot:
            snapshot["event_id"] = snapshot.pop("id")
        safe_events.append(snapshot)
    visible_risks = []
    for risk in risks:
        value = risk if isinstance(risk, dict) else asdict(risk)
        if value.get("module") in {module, "erp"}:
            if "admin.audit.view" in permissions:
                probable_causes = list(value.get("probable_causes", ()))
                recommended_actions = list(value.get("recommended_actions", ()))
                impact = (
                    f"Pode afetar a estabilidade de {value['module']}."
                    f"{value['operation']} enquanto o sinal persistir."
                )
                visible_risks.append({
                    "module": value["module"], "level": value["severity"],
                    "operation": value["operation"],
                    "fingerprint": value["fingerprint"],
                    "score": value["score"],
                    "confidence": value["confidence"],
                    "summary": f"Risco {value['severity'].lower()} em {value['module']}.{value['operation']}",
                    "evidence": list(value.get("evidence", ()))[:4],
                    "probable_cause": probable_causes[0] if probable_causes else "Sinal técnico ainda inconclusivo.",
                    "impact": impact,
                    "recommendation": recommended_actions[0] if recommended_actions else "Acompanhar o módulo e repetir a verificação.",
                })
            else:
                visible_risks.append({
                    "module": value["module"], "level": value["severity"],
                    "summary": "Há sinais de instabilidade nesta operação. A Nexa pode orientar.",
                    "evidence": [],
                })
        if len(visible_risks) >= 3:
            break
    tools = {
        "get_erp_context": {
            "product": "NexPoint ERP",
            "environment": settings.environment,
            "version": settings.version[:40],
            "build": settings.build[:40],
        },
        "get_current_user_context": {"role": role},
        "get_current_module_context": {"module": module, "screen": screen_name, "operation": operation},
        "get_user_permissions_context": {"permissions": public_permissions},
        "get_module_health": {"module": module, "status": "degraded" if visible_risks else "normal", "risks": visible_risks},
        "get_recent_diagnostic_events": {"events": safe_events},
        "search_erp_help": {"results": public_help(query, permissions)},
    }
    if admin_lock_status is not None:
        tools["get_admin_lock_status"] = admin_lock_status
    if monitor is not None and "admin.audit.view" in permissions:
        # The bridge accepts no model-provided filters.  Every log tool carries
        # the same server-derived scope and an already bounded snapshot.
        log_scope = {
            "tenant_id": str(monitor.tenant_id),
            "installation_id": str(monitor.installation_id),
        }
        selected = safe_events[0] if safe_events else None
        selected_correlation = (
            selected.get("correlation_id") if isinstance(selected, dict) else None
        )
        timeline = [
            event for event in safe_events
            if selected_correlation is None
            or event.get("correlation_id") == selected_correlation
        ][:5]
        selected_fingerprint = (
            selected.get("fingerprint") if isinstance(selected, dict) else None
        )
        fingerprint_events = [
            event for event in safe_events
            if selected_fingerprint is not None
            and event.get("fingerprint") == selected_fingerprint
        ][:5]
        fingerprint_summary = {
            "fingerprint": selected_fingerprint,
            "occurrence_count": len(fingerprint_events),
            "first_seen_at": (
                fingerprint_events[-1].get("timestamp")
                if fingerprint_events else None
            ),
            "last_seen_at": (
                fingerprint_events[0].get("timestamp")
                if fingerprint_events else None
            ),
            "module": selected.get("module") if isinstance(selected, dict) else None,
        }
        tools.update({
            "search_erp_logs": {
                "scope": dict(log_scope),
                "filters_applied": {"severity": ["ERROR", "CRITICAL", "WARNING"]},
                "events": safe_events[:5],
            },
            "get_log_timeline": {
                "scope": dict(log_scope),
                "correlation_id": selected_correlation,
                "events": timeline,
            },
            "get_error_fingerprint": {
                "scope": dict(log_scope),
                "fingerprint": selected_fingerprint,
                "summary": fingerprint_summary,
                "sample_events": fingerprint_events,
            },
            "get_recent_errors": {
                "scope": dict(log_scope),
                "events": [
                    event for event in safe_events
                    if event.get("severity") in {"ERROR", "CRITICAL"}
                ][:5],
            },
            "get_incident_diagnostics": {
                "scope": dict(log_scope),
                "timeline": timeline,
                "fingerprint": fingerprint_summary,
                "version": {
                    "app_version": settings.version[:40],
                    "build": settings.build[:40],
                },
                "health": {
                    "module": module,
                    "status": "degraded" if visible_risks else "normal",
                },
                "correlated_events": timeline,
            },
        })
    if "admin.system.view" in permissions and any(token in str(query).casefold() for token in ("doctor", "preflight", "diagnóstico do sistema", "integridade")):
        from app.services.erp_preflight import run_preflight
        report = run_preflight(request.app.state.engine)
        tools["run_erp_preflight"] = {
            "status": report.status,
            "findings": [{"check": finding.check, "status": finding.status, "reason": finding.reason} for finding in report.findings],
        }
    return pseudonymous_user_id(request.app.state.nexa_secret, user.id), context, tools
