from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit
import logging
import mimetypes

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.core.config import ROOT_DIR, Settings, development_credentials, get_settings
from app.core.database import build_engine, build_session_factory
from app.repositories import AuthRepository
from app.routes import (
    admin_router,
    auth_router,
    cash_router,
    customers_router,
    notes_router,
    pages_router,
    payments_router,
    payment_configuration_router,
    services_admin_router,
    services_router,
)
from app.routes.helpers import branding_context, navigation_context, templates
from app.services.auth import AuthService
from app.services.bootstrap import initialize_database


SESSION_MAX_AGE_SECONDS = 60 * 60 * 12
UNSAFE_HTTP_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
LOCAL_ALLOWED_HOSTS = ("127.0.0.1", "localhost", "testserver")


def _same_origin(request: Request) -> bool:
    reference = request.headers.get("origin") or request.headers.get("referer")
    if not reference:
        # Clientes não navegador (e alguns runtimes webview) podem omitir os
        # cabeçalhos. SameSite=Strict ainda protege o cookie nesses casos.
        return True
    try:
        parsed = urlsplit(reference)
        hostname = parsed.hostname
    except ValueError:
        return False
    expected_host = request.headers.get("host", "").lower()
    return (
        parsed.scheme == "http"
        and bool(hostname)
        and parsed.netloc.lower() == expected_host
        and parsed.username is None
        and parsed.password is None
    )


def create_app(
    *,
    database_url: str | None = None,
    credentials: dict[str, str] | None = None,
    session_secret: str | None = None,
) -> FastAPI:
    base_settings = get_settings() if database_url is None else Settings(
        database_url=database_url,
        session_secret=session_secret or "test-session-secret-with-at-least-32-chars",
    )
    engine = build_engine(database_url or base_settings.database_url)
    factory = build_session_factory(engine)
    seed_credentials = credentials or development_credentials()
    initialize_database(engine, factory, seed_credentials, base_settings)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        engine.dispose()

    app = FastAPI(
        title=base_settings.app_name,
        version=base_settings.version,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.settings = base_settings
    app.state.engine = engine
    app.state.session_factory = factory
    @app.middleware("http")
    async def load_current_user(request: Request, call_next):
        request.state.current_user = None
        user_id = request.session.get("user_id")
        if type(user_id) is int and 0 < user_id < 2**63:
            with factory() as session:
                request.state.current_user = AuthService(AuthRepository(session)).load(user_id)
                if request.state.current_user is None:
                    request.session.clear()
        public = request.url.path in {"/login", "/health"} or request.url.path.startswith("/static/")
        if not public and request.state.current_user is None:
            return RedirectResponse("/login", status_code=303)
        return await call_next(request)

    @app.middleware("http")
    async def enforce_local_request_security(request: Request, call_next):
        if request.method in UNSAFE_HTTP_METHODS and not _same_origin(request):
            return PlainTextResponse("Solicitação local inválida.", status_code=403)
        response = await call_next(request)
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; base-uri 'self'; form-action 'self'; "
            "frame-ancestors 'none'; object-src 'none'; connect-src 'self'; "
            "img-src 'self' data:; font-src 'self'; "
            "script-src 'self'; style-src 'self' 'unsafe-inline'",
        )
        # Chromium pode enviar Origin:null em POST de navegação com no-referrer.
        # same-origin preserva a origem local e não envia Referer a outros sites.
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        return response

    # Adicionado depois do middleware funcional para permanecer como a camada
    # externa: request.session já existe quando load_current_user é executado.
    app.add_middleware(
        SessionMiddleware,
        secret_key=session_secret or base_settings.session_secret,
        session_cookie="erp_local_session",
        same_site="strict",
        https_only=False,
        max_age=SESSION_MAX_AGE_SECONDS,
    )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(LOCAL_ALLOWED_HOSTS))

    # O registro MIME do Windows pode classificar .js como text/plain.
    # Tipos explícitos mantêm nosniff sem bloquear os componentes locais.
    mimetypes.init()
    mimetypes.add_type("text/javascript", ".js")
    mimetypes.add_type("text/css", ".css")
    app.mount("/static", StaticFiles(directory=ROOT_DIR / "static"), name="static")
    app.include_router(auth_router)
    app.include_router(cash_router)
    app.include_router(customers_router)
    # As rotas literais de Notas precisam preceder /servicos/{service_id}.
    app.include_router(notes_router)
    # Pagamentos usa caminhos mais específicos sob /servicos/notas.
    app.include_router(payments_router)
    app.include_router(admin_router)
    app.include_router(payment_configuration_router)
    app.include_router(services_admin_router)
    app.include_router(services_router)
    app.include_router(pages_router)

    @app.exception_handler(403)
    async def forbidden(request: Request, _exception):
        if request.state.current_user is None:
            return RedirectResponse("/login", status_code=303)
        with factory() as session:
            context = navigation_context(request, session, page_title="Acesso restrito")
            return templates.TemplateResponse(request, "errors/403.html", context, status_code=403)

    @app.exception_handler(404)
    async def not_found(request: Request, _exception):
        try:
            with factory() as session:
                context = branding_context(request, session)
        except Exception:
            settings = request.app.state.settings
            context = {
                "app_name": settings.app_name,
                "company_name": settings.company_name,
                "app_version": settings.version,
                "logo_path": settings.logo_path,
            }
        return templates.TemplateResponse(
            request,
            "errors/404.html",
            context,
            status_code=404,
        )

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, _exception):
        # Não registrar payloads, SQL, cookies, senhas ou mensagens de exceção.
        logging.getLogger("erp.errors").error("Falha interna (%s)", type(_exception).__name__)
        try:
            with factory() as session:
                context = branding_context(request, session)
        except Exception:
            settings = request.app.state.settings
            context = {
                "app_name": settings.app_name,
                "company_name": settings.company_name,
                "app_version": settings.version,
                "logo_path": settings.logo_path,
            }
        return templates.TemplateResponse(
            request,
            "errors/500.html",
            context,
            status_code=500,
        )

    return app
