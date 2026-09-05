from __future__ import annotations

from app.core.permissions import CurrentUser
from app.core.security import verify_password
from app.repositories import AuthRepository


class AuthService:
    def __init__(self, repository: AuthRepository):
        self.repository = repository

    @staticmethod
    def current(user) -> CurrentUser:
        permissions = frozenset(permission.code for role in user.roles for permission in role.permissions)
        return CurrentUser(
            id=user.id,
            email=user.email,
            display_name=user.display_name,
            roles=frozenset(role.code for role in user.roles),
            permissions=permissions,
        )

    def authenticate(self, email: str, password: str) -> CurrentUser | None:
        user = self.repository.user_by_email(email.strip().lower())
        if user is None or not user.active or not verify_password(password, user.password_hash):
            return None
        return self.current(user)

    def load(self, user_id: int) -> CurrentUser | None:
        user = self.repository.user_by_id(user_id)
        return self.current(user) if user and user.active else None
