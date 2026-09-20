from __future__ import annotations

from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
import json
import secrets

from fastapi import HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.security import hash_password, verify_password
from app.models.admin_lock import AdminLock
from app.models.auth import AuditEvent
from app.observability.context import emit_observability_event
from app.services.authorization import (
    ActorAuthorizationError,
    active_actor_access,
    require_effective_actor_permission,
)


PROCESS_UNLOCK_GENERATION = secrets.token_urlsafe(32)
SESSION_VERSION = "admin_lock_version"
SESSION_UNTIL = "admin_lock_until"
SESSION_PROCESS = "admin_lock_process"
MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 256
DEFAULT_TIMEOUT_MINUTES = 15


class AdminLockError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class AdminLockStatus:
    configured: bool
    failed_attempt_count: int
    lockout_until: datetime | None
    last_recovery_at: datetime | None


def _now_timestamp() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def clear_admin_unlock(request: Request) -> None:
    for key in (SESSION_VERSION, SESSION_UNTIL, SESSION_PROCESS):
        request.session.pop(key, None)


def has_admin_area_access(user) -> bool:
    """Indica acesso potencial à área sem substituir o check de cada rota."""

    return any(
        code.startswith("admin.") or code.startswith("finance.")
        for code in user.permissions
    )


def admin_unlock_valid(request: Request, lock: AdminLock | None) -> bool:
    if lock is None:
        return False
    version = request.session.get(SESSION_VERSION)
    until = request.session.get(SESSION_UNTIL)
    generation = request.session.get(SESSION_PROCESS)
    valid = (
        type(version) is int
        and version == lock.version
        and type(until) is int
        and until > _now_timestamp()
        and isinstance(generation, str)
        and secrets.compare_digest(generation, PROCESS_UNLOCK_GENERATION)
    )
    if not valid:
        clear_admin_unlock(request)
    return valid


def require_admin_unlock(request: Request) -> None:
    user = getattr(request.state, "current_user", None)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    if not has_admin_area_access(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    with request.app.state.session_factory() as session:
        lock = session.get(AdminLock, 1)
        if lock is None:
            if "admin" in user.roles:
                raise HTTPException(
                    status_code=status.HTTP_303_SEE_OTHER,
                    headers={"Location": "/admin/cadeado/configurar"},
                )
            raise HTTPException(status_code=status.HTTP_423_LOCKED)
        if not admin_unlock_valid(request, lock):
            destination = request.url.path
            raise HTTPException(
                status_code=status.HTTP_303_SEE_OTHER,
                headers={"Location": f"/admin/cadeado/desbloquear?next={destination}"},
            )


class AdminLockService:
    def __init__(
        self,
        session: Session,
        *,
        max_attempts: int = 5,
        lockout_minutes: int = 5,
    ):
        self.session = session
        self.max_attempts = max(3, min(int(max_attempts), 20))
        self.lockout_minutes = max(1, min(int(lockout_minutes), 60))

    def get(self) -> AdminLock | None:
        return self.session.get(AdminLock, 1)

    def _owner(self, actor_id: int) -> None:
        try:
            access = active_actor_access(self.session, actor_id)
        except ActorAuthorizationError as exc:
            raise AdminLockError(str(exc)) from None
        if not access.is_owner or "admin.overview.view" not in access.permissions:
            raise AdminLockError("Somente um Proprietário ativo pode configurar o cadeado.")

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc).replace(tzinfo=None)

    def status(self) -> AdminLockStatus:
        lock = self.get()
        if lock is None:
            return AdminLockStatus(False, 0, None, None)
        return AdminLockStatus(
            True,
            lock.failed_attempt_count,
            lock.lockout_until,
            lock.last_recovery_at,
        )

    @staticmethod
    def _validate_new(password: str, confirmation: str) -> None:
        if not MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH:
            raise AdminLockError("A senha deve ter entre 8 e 256 caracteres.")
        if password != confirmation:
            raise AdminLockError("A confirmação da senha não confere.")

    @classmethod
    def validate_new_password(cls, password: str, confirmation: str) -> None:
        """Validate locally before a one-use recovery authorization is consumed."""

        cls._validate_new(password, confirmation)

    def validate_authorized_reset_request(
        self, actor_id: int, password: str, confirmation: str
    ) -> None:
        """Check every deterministic local precondition before consuming a token."""

        self._owner(actor_id)
        self._validate_new(password, confirmation)
        if self.get() is None:
            raise AdminLockError("O cadeado administrativo ainda não foi configurado.")

    def configure(
        self, actor_id: int, password: str, confirmation: str, timeout_minutes: int
    ) -> AdminLock:
        self._owner(actor_id)
        self._validate_new(password, confirmation)
        if not 1 <= timeout_minutes <= 480:
            raise AdminLockError("O tempo de desbloqueio deve ficar entre 1 e 480 minutos.")
        if self.get() is not None:
            raise AdminLockError("O cadeado administrativo já foi configurado.")
        lock = AdminLock(
            id=1,
            password_hash=hash_password(password),
            timeout_minutes=timeout_minutes,
            configured_by=actor_id,
        )
        self.session.add(lock)
        self.session.add(AuditEvent(
            user_id=actor_id,
            action="admin.lock_configured",
            resource="admin_lock",
            details=json.dumps({"timeout_minutes": timeout_minutes}, sort_keys=True),
        ))
        self.session.commit()
        emit_observability_event(
            module="admin", component="admin_lock_service",
            event_type="admin.lock.configured", operation="configure",
            status="completed", user_id=actor_id, sync_required=True,
        )
        return lock

    def unlock(self, actor_id: int, password: str) -> AdminLock | None:
        try:
            access = active_actor_access(self.session, actor_id)
            if access.role_codes == frozenset({"support"}):
                # A concessão temporária é recalculada no banco no instante do
                # desbloqueio. Ela permite atravessar o segundo fator, mas não
                # amplia o pequeno escopo de leitura definido para o suporte.
                access = require_effective_actor_permission(
                    self.session,
                    actor_id,
                    "admin.overview.view",
                )
        except ActorAuthorizationError as exc:
            raise AdminLockError(str(exc)) from None
        if not any(
            code.startswith("admin.") or code.startswith("finance.")
            for code in access.permissions
        ):
            raise AdminLockError("O usuário não possui acesso à Administração.")
        lock = self.get()
        if lock is None:
            return None
        now = self._now()
        if lock.lockout_until is not None and lock.lockout_until > now:
            self.session.add(AuditEvent(
                user_id=actor_id,
                action="admin.lock_unlock_blocked",
                resource="admin_lock",
                details=json.dumps({"lockout_until": lock.lockout_until.isoformat()}, sort_keys=True),
            ))
            self.session.commit()
            emit_observability_event(
                module="admin", component="admin_lock_service",
                event_type="admin.lock.lockout", operation="unlock",
                status="blocked", user_id=actor_id, severity="WARNING",
                error_code="rate_limited", sync_required=True,
            )
            raise AdminLockError(
                f"Muitas tentativas. Tente novamente após {lock.lockout_until.strftime('%H:%M')}."
            )
        if lock.lockout_until is not None:
            lock.lockout_until = None
            lock.failed_attempt_count = 0
        success = bool(password and len(password) <= MAX_PASSWORD_LENGTH and verify_password(password, lock.password_hash))
        if success:
            lock.failed_attempt_count = 0
            lock.lockout_until = None
        else:
            lock.failed_attempt_count += 1
            if lock.failed_attempt_count >= self.max_attempts:
                lock.lockout_until = now + timedelta(minutes=self.lockout_minutes)
        self.session.add(AuditEvent(
            user_id=actor_id,
            action="admin.lock_unlock_succeeded" if success else "admin.lock_unlock_failed",
            resource="admin_lock",
        ))
        self.session.commit()
        emit_observability_event(
            module="admin", component="admin_lock_service",
            event_type=(
                "admin.lock.unlocked" if success
                else ("admin.lock.lockout" if lock.lockout_until else "admin.lock.failed_attempt")
            ),
            operation="unlock",
            status="completed" if success else "rejected",
            user_id=actor_id,
            severity="INFO" if success else "WARNING",
            error_code="none" if success else "invalid_credential",
            sync_required=True,
        )
        return lock if success else None

    def change(
        self,
        actor_id: int,
        current_password: str,
        new_password: str,
        confirmation: str,
        timeout_minutes: int,
    ) -> AdminLock:
        self._owner(actor_id)
        self._validate_new(new_password, confirmation)
        if not 1 <= timeout_minutes <= 480:
            raise AdminLockError("O tempo de desbloqueio deve ficar entre 1 e 480 minutos.")
        lock = self.get()
        if lock is None or not verify_password(current_password, lock.password_hash):
            raise AdminLockError("A senha administrativa atual está incorreta.")
        lock.password_hash = hash_password(new_password)
        lock.timeout_minutes = timeout_minutes
        lock.version += 1
        lock.configured_by = actor_id
        lock.failed_attempt_count = 0
        lock.lockout_until = None
        lock.recovery_failed_attempt_count = 0
        lock.recovery_lockout_until = None
        self.session.add(AuditEvent(
            user_id=actor_id,
            action="admin.lock_password_changed",
            resource="admin_lock",
            details=json.dumps({"timeout_minutes": timeout_minutes}, sort_keys=True),
        ))
        self.session.commit()
        return lock

    def complete_authorized_reset(
        self,
        actor_id: int,
        new_password: str,
        confirmation: str,
    ) -> AdminLock:
        self._owner(actor_id)
        self._validate_new(new_password, confirmation)
        lock = self.get()
        if lock is None:
            raise AdminLockError("O cadeado administrativo ainda não foi configurado.")
        now = self._now()
        lock.password_hash = hash_password(new_password)
        lock.version += 1
        lock.configured_by = actor_id
        lock.failed_attempt_count = 0
        lock.lockout_until = None
        lock.recovery_failed_attempt_count = 0
        lock.recovery_lockout_until = None
        lock.last_recovery_at = now
        self.session.add(AuditEvent(
            user_id=actor_id,
            action="admin.password.reset_completed",
            resource="admin_lock",
            details=json.dumps({
                "method": "remote_authorization",
            }, sort_keys=True),
        ))
        self.session.commit()
        emit_observability_event(
            module="admin", component="admin_lock_service",
            event_type="admin.password.reset_completed", operation="authorized_reset",
            status="completed", user_id=actor_id, sync_required=True,
        )
        return lock


def mark_admin_unlocked(request: Request, lock: AdminLock) -> None:
    request.session[SESSION_VERSION] = lock.version
    request.session[SESSION_UNTIL] = _now_timestamp() + int(
        timedelta(minutes=lock.timeout_minutes).total_seconds()
    )
    request.session[SESSION_PROCESS] = PROCESS_UNLOCK_GENERATION
