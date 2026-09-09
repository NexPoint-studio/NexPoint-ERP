from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timezone
import json
import math
import re
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models import AuditEvent, User
from app.services.authorization import require_effective_actor_permission


REDACTED = "[conteúdo protegido]"
SENSITIVE_KEY = re.compile(
    r"password|senha|token|secret|segredo|cookie|authorization|credential|credencial|private.?key|session_generation",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class AuditRow:
    event: AuditEvent
    actor_name: str
    actor_email: str | None
    safe_details: str | None


@dataclass(frozen=True, slots=True)
class AuditPage:
    rows: tuple[AuditRow, ...]
    total: int
    page: int
    per_page: int
    pages: int
    actions: tuple[str, ...]


def _redact(value):
    if isinstance(value, dict):
        return {
            str(key): REDACTED if SENSITIVE_KEY.search(str(key)) else _redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def safe_audit_details(raw: str | None) -> str | None:
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        rendered = str(raw)[:2000]
        return REDACTED if SENSITIVE_KEY.search(rendered) else rendered
    return json.dumps(_redact(parsed), ensure_ascii=False, sort_keys=True)[:2000]


def _utc_boundary(raw: str, timezone_name: str, *, end: bool) -> datetime:
    parsed = date.fromisoformat(raw)
    boundary = datetime.combine(parsed, time.max if end else time.min, ZoneInfo(timezone_name))
    return boundary.astimezone(timezone.utc).replace(tzinfo=None)


class AuditService:
    def __init__(self, session: Session, timezone_name: str):
        self.session = session
        self.timezone_name = timezone_name

    def list_events(
        self,
        actor_id: int,
        *,
        query: str = "",
        action: str = "",
        user_id: int | None = None,
        date_from: str = "",
        date_to: str = "",
        page: int = 1,
        per_page: int = 50,
    ) -> AuditPage:
        require_effective_actor_permission(self.session, actor_id, "admin.audit.view")
        page = max(1, int(page))
        per_page = int(per_page)
        if per_page not in {25, 50, 100}:
            per_page = 50
        query = query.strip()[:160]
        action = action.strip()[:80]

        filters = []
        if query:
            pattern = f"%{query.replace('%', r'\%').replace('_', r'\_')}%"
            filters.append(or_(
                AuditEvent.action.ilike(pattern, escape="\\"),
                AuditEvent.resource.ilike(pattern, escape="\\"),
            ))
        if action:
            filters.append(AuditEvent.action == action)
        if user_id is not None:
            filters.append(AuditEvent.user_id == user_id)
        if date_from:
            filters.append(AuditEvent.created_at >= _utc_boundary(date_from, self.timezone_name, end=False))
        if date_to:
            filters.append(AuditEvent.created_at <= _utc_boundary(date_to, self.timezone_name, end=True))

        total = int(self.session.scalar(select(func.count(AuditEvent.id)).where(*filters)) or 0)
        pages = max(1, math.ceil(total / per_page))
        page = min(page, pages)
        statement = (
            select(AuditEvent, User.display_name, User.email)
            .outerjoin(User, User.id == AuditEvent.user_id)
            .where(*filters)
            .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
            .offset((page - 1) * per_page)
            .limit(per_page)
        )
        rows = tuple(
            AuditRow(
                event=event,
                actor_name=display_name or "Sistema/usuário removido",
                actor_email=email,
                safe_details=safe_audit_details(event.details),
            )
            for event, display_name, email in self.session.execute(statement)
        )
        actions = tuple(self.session.scalars(
            select(AuditEvent.action).distinct().order_by(AuditEvent.action)
        ))
        return AuditPage(rows, total, page, per_page, pages, actions)
