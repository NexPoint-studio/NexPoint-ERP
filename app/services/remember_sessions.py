from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import hmac
import json
import re
import secrets
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.permissions import CurrentUser
from app.models import AuditEvent, RememberSession
from app.observability.context import emit_observability_event
from app.repositories import AuthRepository
from app.services.auth import AuthService


COOKIE_VERSION = "v1"
_COOKIE_PATTERN = re.compile(
    r"^v1\.([0-9a-f]{32})\.([A-Za-z0-9_-]{40,128})$"
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _token_digest(session_id: str, secret: str) -> str:
    return _digest(f"nexpoint-remember-session:v1:{session_id}:{secret}")


def _cookie_value(session_id: str, secret: str) -> str:
    return f"{COOKIE_VERSION}.{session_id}.{secret}"


def remember_cookie_name(session_cookie: str) -> str:
    return f"{session_cookie}_remember"


@dataclass(frozen=True, slots=True)
class RememberRestore:
    current_user: CurrentUser | None
    status: str
    replacement_cookie: str | None = None


class RememberSessionService:
    """Issue and validate opaque, revocable, installation-bound login tokens."""

    def __init__(self, session: Session):
        self.session = session

    @staticmethod
    def _parse(raw_cookie: object) -> tuple[str, str] | None:
        value = str(raw_cookie or "").strip()
        match = _COOKIE_PATTERN.fullmatch(value)
        return (match.group(1), match.group(2)) if match else None

    @staticmethod
    def _audit_details(reason: str) -> str:
        return json.dumps({"reason": reason}, sort_keys=True, separators=(",", ":"))

    def _finish(
        self,
        row: RememberSession,
        *,
        status: str,
        now: datetime,
        action: str,
        reason: str,
    ) -> None:
        row.status = status
        row.revoked_at = now
        self.session.add(AuditEvent(
            user_id=row.user_id,
            action=action,
            resource="remember_session",
            details=self._audit_details(reason),
        ))

    def create(
        self,
        user: CurrentUser,
        *,
        installation_id: str,
        session_generation: str,
        lifetime_days: int,
    ) -> str:
        now = _utcnow()
        lifetime = max(1, min(int(lifetime_days), 180))
        # A new opt-in on the same installation supersedes older credentials
        # for this user without affecting another ERP user on the computer.
        active = tuple(self.session.scalars(
            select(RememberSession).where(
                RememberSession.user_id == user.id,
                RememberSession.installation_id == installation_id,
                RememberSession.status == "ACTIVE",
            )
        ))
        for row in active:
            self._finish(
                row,
                status="REVOKED",
                now=now,
                action="auth.session.revoked",
                reason="superseded_by_new_remember_session",
            )
        session_id = uuid4().hex
        secret = secrets.token_urlsafe(32)
        self.session.add(RememberSession(
            id=session_id,
            user_id=user.id,
            token_hash=_token_digest(session_id, secret),
            installation_id=str(installation_id)[:80],
            auth_version=user.auth_version,
            session_generation_hash=_digest(session_generation),
            status="ACTIVE",
            created_at=now,
            expires_at=now + timedelta(days=lifetime),
            last_used_at=now,
            rotated_at=now,
        ))
        self.session.add(AuditEvent(
            user_id=user.id,
            action="auth.remember_session.created",
            resource="remember_session",
            details=json.dumps(
                {"lifetime_days": lifetime},
                sort_keys=True,
                separators=(",", ":"),
            ),
        ))
        self.session.commit()
        emit_observability_event(
            module="auth",
            component="remember_session_service",
            event_type="auth.remember_session.created",
            operation="create",
            status="completed",
            user_id=user.id,
            sync_required=True,
        )
        return _cookie_value(session_id, secret)

    def restore(
        self,
        raw_cookie: object,
        *,
        installation_id: str,
        session_generation: str,
        rotation_days: int,
    ) -> RememberRestore:
        parsed = self._parse(raw_cookie)
        if parsed is None:
            return RememberRestore(None, "INVALID")
        session_id, secret = parsed
        row = self.session.get(RememberSession, session_id)
        if row is None or not hmac.compare_digest(
            row.token_hash, _token_digest(session_id, secret)
        ):
            # A guessed public id plus an invalid secret must not revoke the
            # legitimate credential and become a denial-of-service primitive.
            return RememberRestore(None, "INVALID")

        now = _utcnow()
        if row.status != "ACTIVE":
            return RememberRestore(None, row.status)
        if row.expires_at <= now:
            self._finish(
                row,
                status="EXPIRED",
                now=now,
                action="auth.remember_session.expired",
                reason="absolute_expiry",
            )
            self.session.commit()
            emit_observability_event(
                module="auth",
                component="remember_session_service",
                event_type="auth.remember_session.expired",
                operation="restore",
                status="expired",
                user_id=row.user_id,
                severity="INFO",
                sync_required=True,
            )
            return RememberRestore(None, "EXPIRED")

        repository = AuthRepository(self.session)
        current_user = AuthService(repository).load(row.user_id)
        valid_binding = (
            hmac.compare_digest(row.installation_id, str(installation_id))
            and hmac.compare_digest(
                row.session_generation_hash, _digest(session_generation)
            )
            and current_user is not None
            and current_user.auth_version == row.auth_version
        )
        if not valid_binding:
            reason = (
                "user_inactive_or_auth_changed"
                if current_user is None or (
                    current_user is not None
                    and current_user.auth_version != row.auth_version
                )
                else "installation_or_generation_changed"
            )
            self._finish(
                row,
                status="REVOKED",
                now=now,
                action="auth.session.revoked",
                reason=reason,
            )
            self.session.commit()
            emit_observability_event(
                module="auth",
                component="remember_session_service",
                event_type="auth.session.revoked",
                operation="restore",
                status="revoked",
                user_id=row.user_id,
                severity="WARNING",
                error_code="session_binding_changed",
                sync_required=True,
            )
            return RememberRestore(None, "REVOKED")

        replacement_cookie = None
        rotation = max(1, min(int(rotation_days), 30))
        if row.rotated_at + timedelta(days=rotation) <= now:
            replacement_secret = secrets.token_urlsafe(32)
            row.token_hash = _token_digest(row.id, replacement_secret)
            row.rotated_at = now
            replacement_cookie = _cookie_value(row.id, replacement_secret)
        row.last_used_at = now
        self.session.add(AuditEvent(
            user_id=row.user_id,
            action="auth.remember_session.restored",
            resource="remember_session",
        ))
        self.session.commit()
        emit_observability_event(
            module="auth",
            component="remember_session_service",
            event_type="auth.remember_session.restored",
            operation="restore",
            status="completed",
            user_id=row.user_id,
            sync_required=True,
        )
        return RememberRestore(current_user, "RESTORED", replacement_cookie)

    def revoke_presented(self, raw_cookie: object, *, reason: str) -> int | None:
        parsed = self._parse(raw_cookie)
        if parsed is None:
            return None
        session_id, secret = parsed
        row = self.session.get(RememberSession, session_id)
        if row is None or not hmac.compare_digest(
            row.token_hash, _token_digest(session_id, secret)
        ):
            return None
        if row.status == "ACTIVE":
            now = _utcnow()
            self._finish(
                row,
                status="REVOKED",
                now=now,
                action="auth.session.revoked",
                reason=reason,
            )
            self.session.commit()
            emit_observability_event(
                module="auth",
                component="remember_session_service",
                event_type="auth.session.revoked",
                operation="revoke",
                status="completed",
                user_id=row.user_id,
                sync_required=True,
            )
        return row.user_id

    def revoke_user_sessions(self, user_id: int, *, reason: str) -> int:
        now = _utcnow()
        rows = tuple(self.session.scalars(
            select(RememberSession).where(
                RememberSession.user_id == user_id,
                RememberSession.status == "ACTIVE",
            )
        ))
        for row in rows:
            self._finish(
                row,
                status="REVOKED",
                now=now,
                action="auth.session.revoked",
                reason=reason,
            )
        return len(rows)

    @staticmethod
    def revoke_user_sessions_sql(session: Session, user_id: int, *, reason: str) -> int:
        """Revoke rows inside an existing security transaction."""

        now = _utcnow()
        result = session.execute(
            update(RememberSession)
            .where(
                RememberSession.user_id == user_id,
                RememberSession.status == "ACTIVE",
            )
            .values(status="REVOKED", revoked_at=now)
        )
        count = int(result.rowcount or 0)
        if count:
            session.add(AuditEvent(
                user_id=user_id,
                action="auth.session.revoked",
                resource="remember_session",
                details=json.dumps(
                    {"reason": reason, "count": count},
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            ))
        return count
