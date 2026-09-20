from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Form, Request, status
from fastapi.responses import RedirectResponse

from app.observability.context import emit_observability_event
from app.repositories import AuthRepository
from app.routes.helpers import branding_context, templates
from app.services.auth import AuthService
from app.services.remember_sessions import (
    RememberSessionService,
    remember_cookie_name,
)


router = APIRouter()


def _short_session_expiry(request: Request) -> int:
    hours = max(1, min(int(request.app.state.settings.session_hours), 168))
    return int((datetime.now(timezone.utc) + timedelta(hours=hours)).timestamp())


@router.get("/login", name="login")
def login_page(request: Request):
    if request.state.current_user:
        return RedirectResponse("/clientes/lista", status_code=status.HTTP_303_SEE_OTHER)
    with request.app.state.session_factory() as session:
        demo_mode = bool(getattr(request.app.state, "demo_mode", False))
        context = {
            "error": None,
            "email": getattr(request.app.state, "demo_owner_login", "") if demo_mode else "",
            "remember": False,
            "demo_mode": demo_mode,
            **branding_context(request, session),
        }
    return templates.TemplateResponse(request, "login.html", context)


@router.post("/login")
def login(
    request: Request,
    email: str = Form("", max_length=180),
    password: str = Form("", max_length=1024),
    remember: str = Form("", max_length=16),
):
    remember_enabled = remember == "1"
    remember_cookie = remember_cookie_name(request.app.state.session_cookie_name)
    persistent_value: str | None = None
    with request.app.state.session_factory() as session:
        repository = AuthRepository(session)
        user = AuthService(repository).authenticate(email, password)
        if user is None:
            repository.audit(None, "auth.login_failed", "session", email.strip().lower())
            repository.audit(None, "auth.login.failed", "session")
            emit_observability_event(
                module="auth",
                component="login_route",
                event_type="auth.login.failed",
                operation="login",
                status="rejected",
                severity="WARNING",
                error_code="invalid_credentials",
                sync_required=True,
            )
            context = {
                "error": "Usuário ou senha inválidos.",
                "email": email,
                "remember": remember_enabled,
                "demo_mode": bool(getattr(request.app.state, "demo_mode", False)),
                **branding_context(request, session),
            }
            return templates.TemplateResponse(
                request, "login.html", context,
                status_code=status.HTTP_401_UNAUTHORIZED,
            )
        session_generation = repository.session_generation()
        if not session_generation:
            raise RuntimeError("A geração local de sessões não está configurada.")
        remember_service = RememberSessionService(session)
        remember_service.revoke_presented(
            request.cookies.get(remember_cookie), reason="new_login_on_device"
        )
        request.session.clear()
        request.session["user_id"] = user.id
        request.session["auth_version"] = user.auth_version
        request.session["session_generation"] = session_generation
        request.session["session_expires_at"] = _short_session_expiry(request)
        if remember_enabled:
            persistent_value = remember_service.create(
                user,
                installation_id=request.app.state.remember_installation_id,
                session_generation=session_generation,
                lifetime_days=request.app.state.settings.remember_session_days,
            )
        repository.audit(user.id, "auth.login", "session")
        repository.audit(user.id, "auth.login.success", "session")
    emit_observability_event(
        module="auth",
        component="login_route",
        event_type="auth.login.success",
        operation="login",
        status="completed",
        user_id=user.id,
        sync_required=True,
    )
    response = RedirectResponse(
        "/clientes/lista", status_code=status.HTTP_303_SEE_OTHER
    )
    if persistent_value is not None:
        response.set_cookie(
            remember_cookie,
            persistent_value,
            max_age=request.app.state.settings.remember_session_days * 24 * 60 * 60,
            httponly=True,
            secure=False,
            samesite="strict",
            path="/",
        )
    elif request.cookies.get(remember_cookie):
        response.delete_cookie(
            remember_cookie, httponly=True, secure=False, samesite="strict", path="/"
        )
    return response


@router.post("/logout", name="logout")
def logout(request: Request):
    user = request.state.current_user
    remember_cookie = remember_cookie_name(request.app.state.session_cookie_name)
    if user:
        with request.app.state.session_factory() as session:
            RememberSessionService(session).revoke_presented(
                request.cookies.get(remember_cookie), reason="explicit_logout"
            )
            AuthRepository(session).invalidate_user_sessions(user.id, "auth.logout")
    request.session.clear()
    emit_observability_event(
        module="auth",
        component="login_route",
        event_type="auth.logout",
        operation="logout",
        status="completed",
        user_id=getattr(user, "id", None),
        sync_required=True,
    )
    response = RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(
        remember_cookie, httponly=True, secure=False, samesite="strict", path="/"
    )
    return response
