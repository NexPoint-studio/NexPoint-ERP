from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.permissions import (
    ADMINISTRATION_PERMISSIONS, SUPPORT_SCOPED_PERMISSIONS, normal_local_user,
    INSTALLATION_CAPABILITIES, INSTALLATION_ADMIN_CAPABILITIES,
)
from app.models.auth import Role, User
from app.repositories.support import SupportRepository


OWNER_ROLE_CODE = "admin"


class ActorAuthorizationError(PermissionError):
    """Falha fechada para autor ausente, inativo ou sem privilégio permanente."""


@dataclass(frozen=True, slots=True)
class ActorAccess:
    user: User
    role_codes: frozenset[str]
    permissions: frozenset[str]
    installation_actor: bool = False

    @property
    def is_owner(self) -> bool:
        return OWNER_ROLE_CODE in self.role_codes


def _administrative_access(session: Session, access: ActorAccess, permission: str) -> ActorAccess:
    """Grant normal users administrative capabilities only in a validated request.

    Non-HTTP internal callers retain their existing permanent-role checks.
    Re-read the lock and account in the operation's transaction, so a stale
    request/cookie cannot survive a concurrent password or identity change.
    """
    if access.installation_actor:
        if permission not in INSTALLATION_ADMIN_CAPABILITIES:
            return access
        from app.services.administration_context import administration_request
        from app.models.admin_lock import AdminLock
        from app.services.admin_lock import admin_unlock_valid, lock_configured
        request = administration_request()
        lock = session.get(AdminLock, 1, populate_existing=True)
        if lock_configured(lock) and (request is None or not admin_unlock_valid(request, lock)):
            raise ActorAuthorizationError("Desbloqueie a Administração para continuar.")
        if request is not None and getattr(request.state.current_user, "id", None) != access.user.id:
            raise ActorAuthorizationError("A identidade da instalação não confere.")
        return access
    if permission not in ADMINISTRATION_PERMISSIONS or not normal_local_user(access.role_codes):
        return access
    from app.services.administration_context import administration_request
    from app.models.admin_lock import AdminLock
    from app.services.admin_lock import admin_unlock_valid, lock_configured
    request = administration_request()
    if request is None:
        return access
    user = getattr(request.state, "current_user", None)
    if user is None or user.id != access.user.id or user.auth_version != access.user.auth_version:
        raise ActorAuthorizationError("A autorização administrativa não está disponível.")
    lock = session.get(AdminLock, 1, populate_existing=True)
    if lock_configured(lock) and not admin_unlock_valid(request, lock):
        raise ActorAuthorizationError("Desbloqueie a Administração para continuar.")
    if normal_local_user(access.role_codes):
        return ActorAccess(access.user, access.role_codes, access.permissions | ADMINISTRATION_PERMISSIONS)
    return access


def active_actor_access(session: Session, actor_id: int) -> ActorAccess:
    """Carrega somente papéis permanentes do ator diretamente do banco.

    Serviços de administração não confiam em permissões presentes no formulário,
    na sessão assinada ou em uma futura concessão temporária de suporte.
    """
    if type(actor_id) is not int or actor_id <= 0:
        raise ActorAuthorizationError("O autor da operação não possui um acesso ativo.")
    actor = session.scalar(
        select(User)
        .where(User.id == actor_id, User.active.is_(True))
        .options(selectinload(User.roles).selectinload(Role.permissions))
    )
    if actor is None:
        raise ActorAuthorizationError("O autor da operação não possui um acesso ativo.")
    from app.services.installation_actor import is_installation_actor
    if is_installation_actor(session, actor_id):
        return ActorAccess(actor, frozenset(), INSTALLATION_CAPABILITIES, installation_actor=True)
    role_codes = frozenset(role.code for role in actor.roles)
    permissions = frozenset(
        permission.code
        for role in actor.roles
        for permission in role.permissions
    )
    return ActorAccess(actor, role_codes, permissions)


def require_active_actor_permission(
    session: Session,
    actor_id: int,
    permission: str,
    *,
    owner_only: bool = False,
) -> ActorAccess:
    access = _administrative_access(session, active_actor_access(session, actor_id), permission)
    if permission not in access.permissions:
        raise ActorAuthorizationError("O autor da operação não possui a permissão necessária.")
    if owner_only and not access.is_owner:
        raise ActorAuthorizationError("Somente um Proprietário ativo pode realizar esta operação.")
    return access


def require_effective_actor_permission(
    session: Session,
    actor_id: int,
    permission: str,
    *,
    now: datetime | None = None,
) -> ActorAccess:
    """Autoriza leitura permanente ou o pequeno escopo temporário de suporte.

    O papel ``support`` nunca usa permissões permanentes. Seu alcance efetivo é
    recalculado no banco e só existe durante uma concessão ativa. Esta função é
    destinada a consultas; toda mutação deve usar
    ``require_active_actor_permission``.
    """
    access = _administrative_access(session, active_actor_access(session, actor_id), permission)
    if access.role_codes == frozenset({"support"}):
        instant = now or datetime.now(timezone.utc)
        if instant.tzinfo is not None:
            instant = instant.astimezone(timezone.utc).replace(tzinfo=None)
        grant = SupportRepository(session).active_for_user(actor_id, instant)
        if grant is not None and permission in SUPPORT_SCOPED_PERMISSIONS:
            return ActorAccess(
                access.user,
                access.role_codes,
                access.permissions | SUPPORT_SCOPED_PERMISSIONS,
            )
        raise ActorAuthorizationError("A concessão temporária de suporte não está ativa.")
    if permission not in access.permissions:
        raise ActorAuthorizationError("O autor da operação não possui a permissão necessária.")
    return access
