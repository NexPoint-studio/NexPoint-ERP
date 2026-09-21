from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit
import logging
import mimetypes
import os
import re
import secrets
import time
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from sqlalchemy.engine import make_url

from app.core.config import ROOT_DIR, Settings, development_credentials, get_settings
from app.core.database import build_engine, build_session_factory
from app.core.installation_identity import (
    InstallationCredentialStore,
    InstallationCredentials,
)
from app.repositories import AuthRepository, ConfigurationRepository
from app.routes import (
    audit_router,
    admin_lock_router,
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
    support_router,
    sync_status_router,
    system_router,
)
from app.routes.helpers import branding_context, navigation_context, templates
from app.services.auth import AuthService
from app.services.authorization import ActorAuthorizationError
from app.services.bootstrap import initialize_database
from app.services.system_maintenance import apply_pending_restore
from app.services.erp_diagnostics import DiagnosticMonitor
from app.services.control_center_adapter import ControlTelemetryAdapter, control_center_identity_for
from app.services.connectivity import ConnectivityService
from app.services.sync_engine import OfflineSyncEngine, SyncWorker
from app.services.sync_remote import (
    LocalSyncRemote,
    SupabaseSyncRemote,
    SyncRemoteError,
)
from app.services.admin_recovery_remote import SupabaseAdminRecoveryRemote
from app.services.support_tickets import ensure_control_center_repository
from app.services.remember_sessions import (
    RememberSessionService,
    remember_cookie_name,
)
from app.routes.nexa import router as nexa_router
from control_center.config import get_control_center_database_path
from app.observability import ObservabilityRetention, ObservabilityStore, bind_observability_context


UNSAFE_HTTP_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
LOCAL_ALLOWED_HOSTS = ("127.0.0.1", "localhost", "testserver")


class _ReconnectingLocalSyncRemote:
    """Reconnect the local sidecar from the worker, never from an ERP request."""

    def __init__(self, app: FastAPI):
        self.app = app

    def send_batch(self, items):
        try:
            repository = ensure_control_center_repository(self.app)
        except Exception as exc:
            raise SyncRemoteError(type(exc).__name__, reachable=False) from exc
        return LocalSyncRemote(
            repository,
            expected_tenant_id=getattr(self.app.state, "control_center_tenant_id", None),
            expected_installation_id=getattr(
                self.app.state, "control_center_installation_id", None
            ),
        ).send_batch(items)


def _sqlite_database_path(database_url: str) -> Path:
    parsed = make_url(database_url)
    if parsed.get_backend_name() != "sqlite" or not parsed.database or parsed.database == ":memory:":
        raise ValueError("A manutenção exige um arquivo SQLite local.")
    return Path(parsed.database).resolve()


def _production_environment(settings: Settings) -> bool:
    return settings.environment.strip().casefold() in {"prod", "production"}


def _path_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


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
    settings_override: Settings | None = None,
    credentials: dict[str, str] | None = None,
    session_secret: str | None = None,
    session_cookie: str = "erp_local_session",
    restore_enabled: bool = True,
    shutdown_callback: Callable[[], None] | None = None,
    control_center_identity: str | None = None,
    control_center_database_path: str | Path | None = None,
) -> FastAPI:
    if settings_override is not None:
        base_settings = settings_override
        if database_url is not None and database_url != base_settings.database_url:
            raise RuntimeError(
                "database_url diverge da configuracao explicita da aplicacao."
            )
        if _production_environment(base_settings) and base_settings.qa_mode:
            raise RuntimeError("ERP_QA_MODE não pode ser habilitado em produção.")
        if base_settings.qa_mode and base_settings.tenant_type.strip().upper() != "TEST":
            raise RuntimeError("ERP_QA_MODE exige ERP_TENANT_TYPE=TEST.")
    elif database_url is None:
        base_settings = get_settings()
    else:
        base_settings = Settings(
            database_url=database_url,
            session_secret=session_secret or "test-session-secret-with-at-least-32-chars",
        )
    effective_database_url = database_url or base_settings.database_url
    database_path = _sqlite_database_path(effective_database_url)
    operational_database_path = (ROOT_DIR / "data" / "erp.sqlite3").resolve()
    production_credentials: InstallationCredentials | None = None
    if _production_environment(base_settings):
        if base_settings.sync_backend != "supabase":
            raise RuntimeError("PROD exige o remote de sincronizacao Supabase.")
        if not base_settings.sync_endpoint:
            raise RuntimeError("PROD exige endpoint de sincronizacao configurado.")
        if not base_settings.installation_credentials_path:
            raise RuntimeError("PROD exige credencial protegida por instalacao.")
        if _path_within(database_path, ROOT_DIR):
            raise RuntimeError("O banco operacional PROD deve ficar fora do projeto.")
        production_credentials = InstallationCredentialStore(
            Path(base_settings.installation_credentials_path)
        ).load()
        base_settings = replace(
            base_settings,
            session_secret=production_credentials.local_session_secret(),
            tenant_type=production_credentials.tenant_kind,
        )
        database_path.parent.mkdir(parents=True, exist_ok=True)
        if not database_path.is_file() or database_path.stat().st_size < 100:
            raise RuntimeError(
                "O banco PROD ainda nao foi instalado. Execute o provisionamento "
                "controlado antes de iniciar o ERP."
            )
    elif base_settings.sync_backend != "local":
        raise RuntimeError("LOCAL e QA exigem o remote local de sincronizacao.")
    explicit_control_path: Path | None = None
    if control_center_database_path is not None:
        explicit_control_path = Path(
            control_center_database_path
        ).expanduser().resolve()
        try:
            aliases_erp = explicit_control_path == database_path or (
                explicit_control_path.exists()
                and database_path.exists()
                and os.path.samefile(explicit_control_path, database_path)
            )
        except OSError as exc:
            raise RuntimeError(
                "Nao foi possivel confirmar o isolamento do Control Center."
            ) from exc
        if aliases_erp:
            raise RuntimeError(
                "O banco do Control Center deve ser separado do banco operacional do ERP."
            )
    if base_settings.qa_mode:
        if "qa" not in database_path.name.casefold():
            raise RuntimeError("O modo QA exige um banco identificado como QA.")
        if database_path == operational_database_path or (
            database_path.exists()
            and operational_database_path.exists()
            and os.path.samefile(database_path, operational_database_path)
        ):
            raise RuntimeError("O modo QA nunca pode usar o banco operacional.")
        if explicit_control_path is None:
            raise RuntimeError(
                "O modo QA exige um banco Control Center isolado e explícito."
            )
        if "qa" not in explicit_control_path.name.casefold():
            raise RuntimeError(
                "O modo QA exige um Control Center identificado como QA."
            )
    backup_root = (
        database_path.parent / "backups"
        if _production_environment(base_settings)
        else ROOT_DIR / "backups"
        if database_url is None and settings_override is None
        else database_path.parent / "backups"
    )
    restore_result = (
        apply_pending_restore(
            database_path=database_path,
            backup_root=backup_root,
            settings=base_settings,
        )
        if restore_enabled
        else None
    )
    engine = build_engine(effective_database_url)
    factory = build_session_factory(engine)
    if credentials is not None:
        seed_credentials = credentials
    elif database_path.is_file() and database_path.stat().st_size >= 100:
        # Uma instalação existente conserva usuários e hashes como dados. A
        # senha do ambiente serve somente para criar a primeira conta local.
        seed_credentials = {}
    else:
        seed_credentials = development_credentials()
    initialize_database(
        engine,
        factory,
        seed_credentials,
        base_settings,
        control_center_identity=control_center_identity,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        worker = getattr(_app.state, "sync_worker", None)
        try:
            if worker is not None:
                worker.start()
                worker.wake()
            yield
        finally:
            if worker is not None:
                worker.stop()
            observability_store = getattr(_app.state, "observability_store", None)
            if observability_store is not None:
                try:
                    observability_store.retention_cleanup()
                    observability_store.close()
                except Exception:
                    logging.getLogger("erp.observability").warning(
                        "Falha na manutenção final da observabilidade."
                    )
            engine.dispose()
            if shutdown_callback is not None:
                shutdown_callback()

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
    app.state.database_path = database_path
    app.state.session_cookie_name = session_cookie
    app.state.backup_root = backup_root
    app.state.restore_result = restore_result
    app.state.nexa_secret = (
        production_credentials.secret
        if production_credentials is not None
        else os.getenv("NEXA_ERP_BRIDGE_SECRET", "").strip()
    )
    app.state.nexa_url = (
        base_settings.nexa_bridge_url
        if production_credentials is not None
        else ""
    )
    app.state.nexa_tenant_id = (
        production_credentials.tenant_id if production_credentials is not None else ""
    )
    app.state.nexa_installation_id = (
        production_credentials.installation_id
        if production_credentials is not None else ""
    )
    with factory() as identity_session:
        persisted_settings = ConfigurationRepository(identity_session).settings()
    if production_credentials is not None:
        observability_tenant_id = production_credentials.tenant_id
        observability_installation_id = production_credentials.installation_id
    else:
        _source_identity, observability_tenant_id, observability_installation_id = (
            control_center_identity_for(persisted_settings)
        )
    app.state.remember_installation_id = observability_installation_id
    observability_database_path = database_path.with_name(
        f"{database_path.stem}_observability.sqlite3"
    )
    try:
        app.state.observability_store = ObservabilityStore(
            observability_database_path,
            operational_database_path=database_path,
            retention=ObservabilityRetention(
                days=base_settings.observability_retention_days,
                max_events=base_settings.observability_max_events,
                max_bytes=base_settings.observability_max_bytes,
            ),
        )
        app.state.observability_store_error = None
    except Exception as exc:
        # A damaged/unavailable diagnostic sidecar must not prevent the
        # operational ERP from starting. Doctor reports the sidecar problem;
        # the monitor keeps a bounded in-memory fallback for this process.
        app.state.observability_store = None
        app.state.observability_store_error = type(exc).__name__
        logging.getLogger("erp.observability").warning(
            "Observability Store indisponivel (%s).", type(exc).__name__
        )
    app.state.observability_database_path = observability_database_path
    app.state.diagnostic_monitor = DiagnosticMonitor(
        environment=base_settings.environment,
        observability_store=app.state.observability_store,
        tenant_id=observability_tenant_id,
        installation_id=observability_installation_id,
        app_version=base_settings.version,
        build=base_settings.build,
        debug_enabled=base_settings.observability_debug,
    )
    app.state.control_center_repository = None
    app.state.admin_recovery_remote = None
    app.state.control_telemetry_adapter = None
    app.state.control_center_tenant_id = observability_tenant_id
    app.state.control_center_installation_id = observability_installation_id
    app.state.connectivity_service = ConnectivityService()
    app.state.sync_engine = None
    app.state.sync_worker = None
    if explicit_control_path is not None:
        app.state.control_center_database_path = explicit_control_path
    try:
        if (
            not _production_environment(base_settings)
            and control_center_database_path is None
            and database_url is None
        ):
            app.state.control_center_database_path = get_control_center_database_path()
        app.state.control_center_repository = ensure_control_center_repository(app)
    except Exception as exc:
        # The sidecar can never prevent the operational ERP from starting.
        logging.getLogger("erp.control_center").warning(
            "Control Center local indisponível (%s).", type(exc).__name__
        )
        app.state.connectivity_service.record_failure(
            reachable=False, detail=type(exc).__name__
        )
    # The durable outbox and its worker must exist even when the sidecar file
    # cannot be opened at startup. Later retries reconnect without restarting ERP.
    app.state.control_telemetry_adapter = ControlTelemetryAdapter(
        app.state.control_center_repository,
        interval_seconds=base_settings.heartbeat_interval_seconds,
        outbox_only=True,
    )
    if production_credentials is not None:
        sync_remote = SupabaseSyncRemote(
            base_settings.sync_endpoint,
            production_credentials,
            timeout_seconds=base_settings.sync_timeout_seconds,
        )
        recovery_endpoint = base_settings.sync_endpoint.removesuffix("/erp-sync") + (
            "/erp-admin-recovery"
        )
        app.state.admin_recovery_remote = SupabaseAdminRecoveryRemote(
            recovery_endpoint,
            production_credentials,
            timeout_seconds=base_settings.sync_timeout_seconds,
        )
    else:
        sync_remote = _ReconnectingLocalSyncRemote(app)
    app.state.sync_engine = OfflineSyncEngine(
        factory, sync_remote,
        connectivity=app.state.connectivity_service,
        diagnostic_monitor=app.state.diagnostic_monitor,
    )
    app.state.sync_worker = SyncWorker(
        app.state.sync_engine,
        before_run=lambda: app.state.control_telemetry_adapter.maybe_publish(app),
        shutdown_timeout_seconds=base_settings.sync_timeout_seconds + 2,
    )
    app.state.control_telemetry_adapter.maybe_publish(app, force=True)

    def apply_remember_cookie(request: Request, response):
        cookie_name = remember_cookie_name(app.state.session_cookie_name)
        replacement = getattr(request.state, "remember_cookie_replacement", None)
        delete = bool(getattr(request.state, "remember_cookie_delete", False))
        if isinstance(replacement, str) and replacement:
            response.set_cookie(
                cookie_name,
                replacement,
                max_age=base_settings.remember_session_days * 24 * 60 * 60,
                httponly=True,
                secure=False,
                samesite="strict",
                path="/",
            )
        elif delete:
            response.delete_cookie(
                cookie_name,
                httponly=True,
                secure=False,
                samesite="strict",
                path="/",
            )
        return response

    @app.middleware("http")
    async def load_current_user(request: Request, call_next):
        started = time.monotonic()
        request_id = str(uuid4())
        correlation_id = str(uuid4())
        request.state.request_id = request_id
        request.state.correlation_id = correlation_id
        request.state.current_user = None
        request.state.remember_cookie_replacement = None
        request.state.remember_cookie_delete = False
        session_ref = request.session.get("observability_session")
        if not isinstance(session_ref, str) or not re.fullmatch(r"[0-9a-f]{32}", session_ref):
            session_ref = secrets.token_hex(16)
            request.session["observability_session"] = session_ref
        monitor = app.state.diagnostic_monitor
        with bind_observability_context(
            correlation_id=correlation_id,
            request_id=request_id,
            session_id=session_ref,
            emitter=monitor.record,
        ):
            user_id = request.session.get("user_id")
            auth_version = request.session.get("auth_version")
            session_generation = request.session.get("session_generation")
            session_expires_at = request.session.get("session_expires_at")
            if (
                type(user_id) is int and 0 < user_id < 2**63
                and type(auth_version) is int and auth_version >= 1
                and isinstance(session_generation, str)
                and type(session_expires_at) is int
                and session_expires_at > int(time.time())
            ):
                with factory() as session:
                    repository = AuthRepository(session)
                    current_user = AuthService(repository).load(user_id)
                    current_generation = repository.session_generation()
                    valid_session = (
                        current_user is not None
                        and current_user.auth_version == auth_version
                        and current_generation is not None
                        and secrets.compare_digest(current_generation, session_generation)
                    )
                    if valid_session:
                        request.state.current_user = current_user
                    else:
                        request.session.clear()
            elif any(
                key in request.session
                for key in ("user_id", "auth_version", "session_generation")
            ):
                request.session.clear()
            if request.state.current_user is None:
                cookie_name = remember_cookie_name(app.state.session_cookie_name)
                raw_remember = request.cookies.get(cookie_name)
                if raw_remember:
                    with factory() as session:
                        repository = AuthRepository(session)
                        current_generation = repository.session_generation()
                        if current_generation:
                            restored = RememberSessionService(session).restore(
                                raw_remember,
                                installation_id=app.state.remember_installation_id,
                                session_generation=current_generation,
                                rotation_days=base_settings.remember_session_rotation_days,
                            )
                        else:
                            restored = None
                    if restored is not None and restored.current_user is not None:
                        request.session.clear()
                        request.session["observability_session"] = session_ref
                        request.session["user_id"] = restored.current_user.id
                        request.session["auth_version"] = restored.current_user.auth_version
                        request.session["session_generation"] = current_generation
                        request.session["session_expires_at"] = int(
                            time.time() + base_settings.session_hours * 60 * 60
                        )
                        request.state.current_user = restored.current_user
                        request.state.remember_cookie_replacement = (
                            restored.replacement_cookie
                        )
                    else:
                        request.state.remember_cookie_delete = True
            public = request.url.path in {"/login", "/health"} or request.url.path.startswith("/static/")
            if not public and request.state.current_user is None:
                response = RedirectResponse("/login", status_code=303)
                response.headers["X-Request-ID"] = request_id
                response.headers["X-Correlation-ID"] = correlation_id
                return apply_remember_cookie(request, response)
            module_name = request.url.path.split("/")[1] or "erp"
            actor_id = getattr(request.state.current_user, "id", None)
            if request.method in UNSAFE_HTTP_METHODS:
                monitor.record(
                    module=module_name, component="fastapi", event_type="http.request.started",
                    operation=request.method.lower(), category="api", severity="INFO",
                    error_code="none", status="started", user_id=actor_id,
                    request_id=request_id, correlation_id=correlation_id,
                    metadata={"method": request.method, "route": request.url.path},
                    sync_required=False,
                )
            try:
                response = await call_next(request)
            except Exception as exc:
                try:
                    monitor.record(
                        module=module_name, component="fastapi", event_type="http.request.failed",
                        operation=request.method.lower(), category="internal", severity="ERROR",
                        error_code="unhandled_exception", status="failed", user_id=actor_id,
                        request_id=request_id, correlation_id=correlation_id,
                        metadata={"exception_type": type(exc).__name__, "method": request.method,
                                  "route": request.url.path},
                    )
                except Exception:
                    pass
                raise
            elapsed = int((time.monotonic() - started) * 1000)
            try:
                interesting = response.status_code >= 500 or response.status_code in {
                    400, 401, 403, 409, 422, 429
                } or elapsed >= 1000
                if interesting:
                    status_code = response.status_code
                    monitor.record(
                        module=module_name, component="fastapi", event_type="http.request.completed",
                        operation=request.method.lower(),
                        category=("auth" if status_code in {401, 403} else
                                  "validation" if status_code in {400, 409, 422} else
                                  "latency" if elapsed >= 1000 and status_code < 500 else "api"),
                        severity="ERROR" if status_code >= 500 else "WARNING",
                        error_code=f"http_{status_code}",
                        status="failed" if status_code >= 400 else "completed",
                        user_id=actor_id, request_id=request_id,
                        correlation_id=correlation_id, duration_ms=elapsed,
                        metadata={"http_status": status_code, "method": request.method,
                                  "route": request.url.path},
                    )
                elif request.method in UNSAFE_HTTP_METHODS:
                    monitor.record(
                        module=module_name, component="fastapi", event_type="http.request.completed",
                        operation=request.method.lower(), category="api", severity="INFO",
                        error_code="none", status="completed", user_id=actor_id,
                        request_id=request_id, correlation_id=correlation_id,
                        duration_ms=elapsed,
                        metadata={"http_status": response.status_code, "method": request.method,
                                  "route": request.url.path}, sync_required=False,
                    )
            except Exception:
                pass
            if app.state.control_telemetry_adapter is not None:
                try:
                    app.state.control_telemetry_adapter.maybe_publish(app)
                except Exception as exc:
                    logging.getLogger("erp.control_center").warning(
                        "Falha ao enfileirar telemetria (%s).", type(exc).__name__
                    )
                else:
                    if app.state.sync_worker is not None:
                        app.state.sync_worker.wake()
            response.headers["X-Request-ID"] = request_id
            response.headers["X-Correlation-ID"] = correlation_id
            return apply_remember_cookie(request, response)

    @app.middleware("http")
    async def enforce_local_request_security(request: Request, call_next):
        if request.method in UNSAFE_HTTP_METHODS and not _same_origin(request):
            request_id = str(uuid4())
            correlation_id = str(uuid4())
            response = PlainTextResponse("Solicitação local inválida.", status_code=403)
            response.headers["X-Request-ID"] = request_id
            response.headers["X-Correlation-ID"] = correlation_id
            try:
                app.state.diagnostic_monitor.record(
                    module="security", component="same_origin",
                    event_type="security.request_rejected", operation=request.method.lower(),
                    category="auth", severity="WARNING", error_code="origin_rejected",
                    status="rejected", request_id=request_id,
                    correlation_id=correlation_id,
                    metadata={"http_status": 403},
                )
            except Exception:
                pass
            return response
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
        secret_key=(
            base_settings.session_secret
            if _production_environment(base_settings)
            else session_secret or base_settings.session_secret
        ),
        session_cookie=session_cookie,
        same_site="strict",
        https_only=False,
        # Sem Max-Age o cookie assinado da sessão comum encerra com o perfil
        # do navegador. A validade de 12h é aplicada no payload assinado; a
        # persistência opcional usa um token opaco separado e revogável.
        max_age=None,
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
    # O fluxo do cadeado precisa permanecer acessível enquanto os demais
    # endpoints administrativos exigem o segundo fator local.
    app.include_router(admin_lock_router)
    app.include_router(admin_router)
    app.include_router(support_router)
    app.include_router(audit_router)
    app.include_router(system_router)
    app.include_router(nexa_router)
    app.include_router(sync_status_router)
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

    @app.exception_handler(ActorAuthorizationError)
    async def domain_forbidden(request: Request, _exception):
        return await forbidden(request, _exception)

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
