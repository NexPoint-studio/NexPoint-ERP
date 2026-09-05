from app.routes.auth import router as auth_router
from app.routes.cash import router as cash_router
from app.routes.customers import router as customers_router
from app.routes.services import router as services_router
from app.routes.pages import router as pages_router

__all__ = ["auth_router", "cash_router", "customers_router", "services_router", "pages_router"]
