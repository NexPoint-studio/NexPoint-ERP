from __future__ import annotations
from dataclasses import dataclass
from fastapi import HTTPException, Request, status

ALL_PERMISSIONS = (
    "cash.operations.view",
    "cash.view", "cash.create", "cash.edit", "cash.cancel",
    "cash.reports.view", "cash.categories.manage",
    "finance.overview.view", "finance.reports.view", "finance.config.manage",
    "payments.receive",
    "customers.view", "customers.create",
    "customers.edit", "customers.deactivate", "customers.activity.create",
    "services.view", "services.create", "services.edit", "services.deactivate",
    "services.prices.manage", "services.categories.manage", "reports.view",
    "notes.view", "notes.create", "notes.edit", "notes.change_status", "notes.cancel",
    "admin.overview.view",
    "admin.services.view", "admin.services.create", "admin.services.edit",
    "admin.services.deactivate", "admin.services.prices.manage",
    "admin.services.categories.manage", "admin.services.units.manage",
    "admin.users", "admin.permissions", "admin.settings",
    "admin.support.manage", "admin.audit.view", "admin.system.view",
    "admin.backups.manage", "admin.restore",
)

# Uma concessao de suporte nunca replica o papel do proprietario. O alcance
# temporario e deliberadamente pequeno e somente de leitura.
SUPPORT_SCOPED_PERMISSIONS = frozenset({
    "admin.overview.view",
    "admin.audit.view",
    "admin.system.view",
})

# Visibility is independent of operational roles. These capabilities are only
# executable after require_permission/route guards validate Admin Lock.
ADMINISTRATION_PERMISSIONS = frozenset(
    code for code in ALL_PERMISSIONS
    if code.startswith(("admin.", "finance.")) or code in {
        "cash.view", "cash.edit", "cash.cancel", "cash.reports.view",
        "cash.categories.manage", "reports.view",
    }
)

# Fixed application capabilities, never assigned to employees or accepted from
# cookies. Legacy role tables remain solely for historical compatibility.
INSTALLATION_CAPABILITIES = frozenset(ALL_PERMISSIONS) - {"admin.users", "admin.permissions"}
INSTALLATION_ADMIN_CAPABILITIES = frozenset(
    code for code in INSTALLATION_CAPABILITIES
    if code.startswith("admin.") or code in {"finance.config.manage", "cash.categories.manage"}
)


def normal_local_user(roles: frozenset[str]) -> bool:
    return bool(roles) and "support" not in roles

@dataclass(frozen=True, slots=True)
class CurrentUser:
    id: int
    email: str
    display_name: str
    roles: frozenset[str]
    permissions: frozenset[str]
    auth_version: int = 1
    role_labels: tuple[str, ...] = ()
    support_grant_uid: str | None = None
    installation_actor: bool = False
    def can(self, permission: str) -> bool:
        return permission in self.permissions or (
            normal_local_user(self.roles) and permission in ADMINISTRATION_PERMISSIONS
        )

def require_permission(request: Request, permission: str) -> CurrentUser:
    user = getattr(request.state, "current_user", None)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    if not user.can(permission):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    if user.installation_actor:
        if permission in INSTALLATION_ADMIN_CAPABILITIES:
            from app.services.admin_lock import require_admin_unlock
            require_admin_unlock(request)
        return user
    operational_cash_permission = (
        permission in {"cash.edit", "cash.cancel"} and permission in user.permissions
    )
    if permission in ADMINISTRATION_PERMISSIONS and not operational_cash_permission:
        from app.services.admin_lock import require_admin_unlock
        require_admin_unlock(request)
    return user
