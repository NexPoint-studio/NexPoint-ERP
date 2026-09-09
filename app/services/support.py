from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json

from sqlalchemy.orm import Session

from app.core.security import verify_password
from app.models.auth import AuditEvent, User
from app.models.support import SupportGrant
from app.repositories.support import SupportRepository
from app.services.authorization import (
    ActorAuthorizationError,
    ActorAccess,
    require_active_actor_permission,
)
from app.services.cash_validation import local_to_utc_naive, project_zone


SUPPORT_PERMISSION = "admin.support.manage"
SUPPORT_ROLE_CODE = "support"
MAX_SUPPORT_WINDOW = timedelta(hours=24)


class SupportValidationError(ValueError):
    def __init__(self, message: str, *, errors: dict[str, str] | None = None):
        super().__init__(message)
        self.errors = errors or {"form": message}


class SupportAuthorizationError(PermissionError):
    pass


class SupportConflictError(RuntimeError):
    pass


class SupportNotFoundError(LookupError):
    pass


@dataclass(frozen=True, slots=True)
class SupportManagementSnapshot:
    support_users: tuple[User, ...]
    grants: tuple[SupportGrant, ...]


def _utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _clean_bounded(
    value: object,
    *,
    field: str,
    label: str,
    maximum: int,
    errors: dict[str, str],
) -> str:
    cleaned = str(value or "").strip()
    if not cleaned:
        errors[field] = f"Informe {label}."
    elif len(cleaned) > maximum:
        errors[field] = f"Use no máximo {maximum} caracteres."
    return cleaned


class SupportService:
    def __init__(self, session: Session):
        self.session = session
        self.repository = SupportRepository(session)

    def _begin_immediate(self) -> None:
        if self.session.in_transaction():
            raise RuntimeError("A concessão de suporte deve iniciar uma transação exclusiva.")
        self.session.connection().exec_driver_sql("begin immediate")

    def _owner(self, actor_id: int, current_password: str | None = None) -> ActorAccess:
        try:
            access = require_active_actor_permission(
                self.session,
                actor_id,
                SUPPORT_PERMISSION,
                owner_only=True,
            )
        except ActorAuthorizationError as exc:
            raise SupportAuthorizationError(str(exc)) from None
        if current_password is not None:
            password = str(current_password)
            if not password or len(password) > 256 or not verify_password(
                password, access.user.password_hash
            ):
                raise SupportAuthorizationError("A senha atual do Proprietário é inválida.")
        return access

    def _audit(
        self,
        actor_id: int,
        action: str,
        grant: SupportGrant,
        details: dict[str, object] | None = None,
    ) -> None:
        safe_details = {
            "grant_uid": grant.grant_uid,
            "support_user_id": grant.support_user_id,
            **(details or {}),
        }
        self.session.add(
            AuditEvent(
                user_id=actor_id,
                action=action,
                resource=f"support_grants/{grant.id}",
                details=json.dumps(safe_details, ensure_ascii=False, sort_keys=True),
            )
        )

    @staticmethod
    def _local_datetime(
        value: object,
        *,
        field: str,
        label: str,
        timezone_name: str,
        errors: dict[str, str],
    ) -> datetime | None:
        raw = str(value or "").strip()
        if not raw:
            errors[field] = f"Informe {label}."
            return None
        try:
            zone = project_zone(timezone_name)
            parsed = datetime.fromisoformat(raw)
            if parsed.tzinfo is not None:
                parsed = parsed.astimezone(zone).replace(tzinfo=None)
            return local_to_utc_naive(parsed, timezone_name)
        except (RuntimeError, ValueError, OverflowError, OSError):
            errors[field] = f"Informe {label} válida."
            return None

    def management_snapshot(self, actor_id: int) -> SupportManagementSnapshot:
        self._owner(actor_id)
        return SupportManagementSnapshot(
            tuple(self.repository.support_users()),
            tuple(self.repository.grants()),
        )

    def create_grant(
        self,
        raw: dict[str, object],
        actor_id: int,
        timezone_name: str,
        *,
        now: datetime | None = None,
    ) -> SupportGrant:
        self._begin_immediate()
        try:
            self._owner(actor_id, str(raw.get("current_password") or ""))
            errors: dict[str, str] = {}
            raw_support_user_id = str(raw.get("support_user_id") or "").strip()
            try:
                support_user_id = int(raw_support_user_id)
                if support_user_id <= 0:
                    raise ValueError
            except ValueError:
                support_user_id = 0
                errors["support_user_id"] = "Selecione um usuário de suporte válido."
            starts_at = self._local_datetime(
                raw.get("starts_at"),
                field="starts_at",
                label="a data e hora inicial",
                timezone_name=timezone_name,
                errors=errors,
            )
            expires_at = self._local_datetime(
                raw.get("expires_at"),
                field="expires_at",
                label="a data e hora final",
                timezone_name=timezone_name,
                errors=errors,
            )
            purpose = _clean_bounded(
                raw.get("purpose"),
                field="purpose",
                label="a finalidade do suporte",
                maximum=500,
                errors=errors,
            )
            instant = _utc_naive(now or datetime.now(timezone.utc))
            if starts_at is not None and expires_at is not None:
                duration = expires_at - starts_at
                if duration <= timedelta(0):
                    errors["expires_at"] = "O término deve ser posterior ao início."
                elif duration > MAX_SUPPORT_WINDOW:
                    errors["expires_at"] = "A concessão pode durar no máximo 24 horas."
                elif expires_at <= instant:
                    errors["expires_at"] = "O término da concessão deve estar no futuro."
            if errors:
                raise SupportValidationError("Revise os dados da concessão.", errors=errors)

            support_user = self.repository.exact_support_user(support_user_id)
            if support_user is None or not support_user.active:
                raise SupportConflictError(
                    "Selecione um usuário ativo que possua somente o papel Suporte."
                )
            permanent_permissions = {
                permission.code
                for role in support_user.roles
                for permission in role.permissions
            }
            if permanent_permissions:
                raise SupportConflictError(
                    "O usuário de suporte não pode possuir permissões permanentes."
                )
            if self.repository.overlapping(starts_at, expires_at) is not None:
                raise SupportConflictError(
                    "Já existe uma concessão de suporte nessa janela de tempo."
                )

            grant = SupportGrant(
                support_user_id=support_user.id,
                authorized_by=actor_id,
                starts_at=starts_at,
                expires_at=expires_at,
                purpose=purpose,
            )
            self.session.add(grant)
            self.session.flush()
            self._audit(
                actor_id,
                "admin.support_grant_created",
                grant,
                {
                    "starts_at": starts_at.isoformat(),
                    "expires_at": expires_at.isoformat(),
                },
            )
            self.session.commit()
            return grant
        except Exception:
            self.session.rollback()
            raise

    def revoke_grant(
        self,
        grant_id: int,
        raw: dict[str, object],
        actor_id: int,
        *,
        now: datetime | None = None,
    ) -> SupportGrant:
        self._begin_immediate()
        try:
            self._owner(actor_id, str(raw.get("current_password") or ""))
            grant = self.repository.grant(grant_id)
            if grant is None:
                raise SupportNotFoundError("Concessão de suporte não encontrada.")
            instant = _utc_naive(now or datetime.now(timezone.utc))
            status = grant.effective_status(instant)
            if status == "REVOKED":
                raise SupportConflictError("A concessão de suporte já foi revogada.")
            if status == "EXPIRED":
                raise SupportConflictError("A concessão de suporte já expirou.")
            errors: dict[str, str] = {}
            reason = _clean_bounded(
                raw.get("revocation_reason"),
                field="revocation_reason",
                label="o motivo da revogação",
                maximum=500,
                errors=errors,
            )
            if errors:
                raise SupportValidationError("Revise a revogação.", errors=errors)
            grant.revoked_at = instant
            grant.revoked_by = actor_id
            grant.revocation_reason = reason
            self._audit(actor_id, "admin.support_grant_revoked", grant)
            self.session.commit()
            return grant
        except Exception:
            self.session.rollback()
            raise
