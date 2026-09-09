from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.auth import Role, User
from app.models.support import SupportGrant


class SupportRepository:
    def __init__(self, session: Session):
        self.session = session

    @staticmethod
    def _valid_id(value: object) -> bool:
        return type(value) is int and value > 0

    @staticmethod
    def _grant_options():
        return (
            selectinload(SupportGrant.support_user),
            selectinload(SupportGrant.authorizer),
            selectinload(SupportGrant.revoker),
        )

    def grant(self, grant_id: int) -> SupportGrant | None:
        if not self._valid_id(grant_id):
            return None
        return self.session.scalar(
            select(SupportGrant)
            .where(SupportGrant.id == grant_id)
            .options(*self._grant_options())
        )

    def grants(self) -> list[SupportGrant]:
        return list(
            self.session.scalars(
                select(SupportGrant)
                .options(*self._grant_options())
                .order_by(SupportGrant.created_at.desc(), SupportGrant.id.desc())
            )
        )

    def exact_support_user(self, user_id: int) -> User | None:
        if not self._valid_id(user_id):
            return None
        user = self.session.scalar(
            select(User)
            .where(User.id == user_id)
            .options(selectinload(User.roles).selectinload(Role.permissions))
        )
        if user is None or {role.code for role in user.roles} != {"support"}:
            return None
        return user

    def support_users(self) -> list[User]:
        users = list(
            self.session.scalars(
                select(User)
                .where(User.active.is_(True))
                .options(selectinload(User.roles).selectinload(Role.permissions))
                .order_by(User.display_name, User.id)
            )
        )
        return [user for user in users if {role.code for role in user.roles} == {"support"}]

    def overlapping(self, starts_at: datetime, expires_at: datetime) -> SupportGrant | None:
        return self.session.scalar(
            select(SupportGrant)
            .where(
                SupportGrant.revoked_at.is_(None),
                SupportGrant.starts_at < expires_at,
                SupportGrant.expires_at > starts_at,
            )
            .order_by(SupportGrant.starts_at, SupportGrant.id)
            .limit(1)
        )

    def active_for_user(self, user_id: int, at: datetime) -> SupportGrant | None:
        if not self._valid_id(user_id):
            return None
        return self.session.scalar(
            select(SupportGrant)
            .where(
                SupportGrant.support_user_id == user_id,
                SupportGrant.revoked_at.is_(None),
                SupportGrant.starts_at <= at,
                SupportGrant.expires_at > at,
            )
            .options(*self._grant_options())
            .order_by(SupportGrant.starts_at.desc(), SupportGrant.id.desc())
            .limit(1)
        )
