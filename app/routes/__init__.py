from app.routes.auth import router as auth_router
from app.routes.admin import router as admin_router
from app.routes.admin_lock import router as admin_lock_router
from app.routes.cash import router as cash_router
from app.routes.customers import router as customers_router
from app.routes.notes import router as notes_router
from app.routes.services import admin_router as services_admin_router, router as services_router
from app.routes.pages import router as pages_router
from app.routes.payments import router as payments_router
from app.routes.payment_configuration import router as payment_configuration_router
from app.routes.audit import router as audit_router
from app.routes.support import router as support_router
from app.routes.system import router as system_router
from app.routes.sync_status import router as sync_status_router

__all__ = [
    "auth_router", "admin_router", "admin_lock_router", "cash_router", "customers_router", "notes_router",
    "services_router", "services_admin_router", "payments_router",
    "payment_configuration_router", "pages_router",
    "audit_router", "support_router", "system_router", "sync_status_router",
]
