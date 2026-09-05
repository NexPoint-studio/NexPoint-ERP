from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import AuditEvent, FeatureFlag, Permission, Role, Setting, User


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
