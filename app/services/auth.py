from __future__ import annotations

from app.core.permissions import CurrentUser, SUPPORT_SCOPED_PERMISSIONS
from app.core.security import verify_password
from app.repositories import AuthRepository


class AuthService:
    def __init__(self, repository: AuthRepository):
        self.repository = repository

    def current(self, user) -> CurrentUser:
        role_codes = frozenset(role.code for role in user.roles)
        permanent_permissions = {
            permission.code
            for role in user.roles
            for permission in role.permissions
        }
        permissions = set(permanent_permissions)
        support_grant = None
        if "support" in role_codes:
            # Um papel de suporte nunca herda uma concessao permanente, mesmo
            # diante de dados antigos ou manipulados diretamente no SQLite.
            permissions.clear()
        if role_codes == frozenset({"support"}):
            support_grant = self.repository.active_support_grant(user.id)
            if support_grant is not None:
                permissions.update(SUPPORT_SCOPED_PERMISSIONS)
        return CurrentUser(
            id=user.id,
            email=user.email,
            display_name=user.display_name,
            roles=role_codes,
            permissions=frozenset(permissions),
            auth_version=user.auth_version,
            role_labels=tuple(sorted(role.name for role in user.roles)),
            support_grant_uid=support_grant.grant_uid if support_grant else None,
        )

    def authenticate(self, email: str, password: str) -> CurrentUser | None:
        user = self.repository.user_by_email(email.strip().lower())
        if user is None or not user.active or not verify_password(password, user.password_hash):
            return None
        return self.current(user)

    def load(self, user_id: int) -> CurrentUser | None:
        user = self.repository.user_by_id(user_id)
        return self.current(user) if user and user.active else None
