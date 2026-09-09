from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.permissions import SUPPORT_SCOPED_PERMISSIONS
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

    @property
    def is_owner(self) -> bool:
        return OWNER_ROLE_CODE in self.role_codes


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
    access = active_actor_access(session, actor_id)
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
    access = active_actor_access(session, actor_id)
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
