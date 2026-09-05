from __future__ import annotations
from dataclasses import dataclass
from fastapi import HTTPException, Request, status

ALL_PERMISSIONS = (
    "cash.view", "cash.create", "cash.edit", "cash.cancel",
    "cash.reports.view", "cash.categories.manage",
    "customers.view", "customers.create",
    "customers.edit", "customers.deactivate", "customers.activity.create",
    "services.view", "services.create", "services.edit", "services.deactivate",
    "services.prices.manage", "services.categories.manage", "reports.view",
    "admin.users", "admin.permissions", "admin.settings",
)

@dataclass(frozen=True, slots=True)
class CurrentUser:
    id: int
    email: str
    display_name: str
    roles: frozenset[str]
    permissions: frozenset[str]
    def can(self, permission: str) -> bool:
        return permission in self.permissions

def require_permission(request: Request, permission: str) -> CurrentUser:
    user = getattr(request.state, "current_user", None)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    if not user.can(permission):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    return user
