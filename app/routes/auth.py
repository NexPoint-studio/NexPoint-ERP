from __future__ import annotations

from fastapi import APIRouter, Form, Request, status
from fastapi.responses import RedirectResponse

from app.repositories import AuthRepository
from app.routes.helpers import branding_context, templates
from app.services.auth import AuthService


router = APIRouter()


@router.get("/login", name="login")
def login_page(request: Request):
    if request.state.current_user:
        return RedirectResponse("/clientes/lista", status_code=status.HTTP_303_SEE_OTHER)
    with request.app.state.session_factory() as session:
        context = {"error": None, **branding_context(request, session)}
    return templates.TemplateResponse(request, "login.html", context)


@router.post("/login")
def login(request: Request, email: str = Form("", max_length=180), password: str = Form("", max_length=1024)):
    with request.app.state.session_factory() as session:
        repository = AuthRepository(session)
        user = AuthService(repository).authenticate(email, password)
        if user is None:
            repository.audit(None, "auth.login_failed", "session", email.strip().lower())
            context = {
                "error": "Usuário ou senha inválidos.",
                "email": email,
                **branding_context(request, session),
            }
            return templates.TemplateResponse(
                request, "login.html", context,
                status_code=status.HTTP_401_UNAUTHORIZED,
            )
        request.session.clear()
        request.session["user_id"] = user.id
        repository.audit(user.id, "auth.login", "session")
    return RedirectResponse("/clientes/lista", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/logout", name="logout")
def logout(request: Request):
    user = request.state.current_user
    if user:
        with request.app.state.session_factory() as session:
            AuthRepository(session).audit(user.id, "auth.logout", "session")
    request.session.clear()
    return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
