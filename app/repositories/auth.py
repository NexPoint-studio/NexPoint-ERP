from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import AuditEvent, FeatureFlag, Permission, Role, Setting, SupportGrant, User


class AuthRepository:
    def __init__(self, session: Session):
        self.session = session

    def user_by_email(self, email: str) -> User | None:
        query = select(User).where(User.email == email.lower()).options(
            selectinload(User.roles).selectinload(Role.permissions)
        )
        return self.session.scalar(query)

    def user_by_id(self, user_id: int) -> User | None:
        query = select(User).where(User.id == user_id).options(
            selectinload(User.roles).selectinload(Role.permissions)
        )
        return self.session.scalar(query)

    def active_support_grant(self, user_id: int) -> SupportGrant | None:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        return self.session.scalar(
            select(SupportGrant)
            .where(
                SupportGrant.support_user_id == user_id,
                SupportGrant.revoked_at.is_(None),
                SupportGrant.starts_at <= now,
                SupportGrant.expires_at > now,
            )
            .order_by(SupportGrant.expires_at.desc(), SupportGrant.id.desc())
            .limit(1)
        )

    def session_generation(self) -> str | None:
        row = self.session.get(Setting, "security.session_generation")
        return row.value if row and row.value else None

    def invalidate_user_sessions(self, user_id: int, action: str) -> bool:
        """Incrementa a versao e audita na mesma transacao serializada."""
        self.session.connection().exec_driver_sql("begin immediate")
        user = self.session.get(User, user_id)
        if user is None:
            self.session.rollback()
            return False
        user.auth_version += 1
        self.session.add(AuditEvent(user_id=user_id, action=action, resource="session"))
        self.session.commit()
        return True

    def audit(self, user_id: int | None, action: str, resource: str, details: str | None = None) -> None:
        self.session.add(AuditEvent(user_id=user_id, action=action, resource=resource, details=details))
        self.session.commit()


class ConfigurationRepository:
    def __init__(self, session: Session):
        self.session = session

    def flags(self) -> dict[str, bool]:
        return {row.key: row.enabled for row in self.session.scalars(select(FeatureFlag))}

    def settings(self) -> dict[str, str]:
        return {row.key: row.value for row in self.session.scalars(select(Setting))}

    def permission(self, code: str) -> Permission | None:
        return self.session.scalar(select(Permission).where(Permission.code == code))
