"""Executado somente pela bateria, dentro de uma cópia descartável."""
from pathlib import Path
import json
import os
import sys
import threading
import time

ROOT = Path.cwd()
sys.path.insert(0, str(ROOT))

from app import create_app
from app.core.security import hash_password
from app.models import Customer, Role, User
from app.services.admin_lock import AdminLockService
from app.services.cash import CashService
from app.services.cash_validation import CashMovementInput
from sqlalchemy import select
import run_desktop


ADMIN_PASSWORD = os.environ["ERP_ADMIN_PASSWORD"]
ADMIN_LOCK_PASSWORD = "CANARY-Desktop-Admin-Lock-2026"
RESET_ADMIN_LOCK_PASSWORD = "CANARY-Desktop-Admin-Reset-2026"

mode = sys.argv[1]
app = create_app()
if mode == "crash":
    server, thread = run_desktop.start_local_server(app, "127.0.0.1", 8877)
    session = app.state.session_factory()
    actor = session.scalar(select(User.id))
    session.add(Customer(type="PERSON", name="Commit preservado", created_by=actor, updated_by=actor))
    session.commit()
    session.add(Customer(type="PERSON", name="Sem commit", created_by=actor, updated_by=actor))
    session.flush()
    (ROOT / "crash-ready.json").write_text(json.dumps({"server_started": server.started}), encoding="utf-8")
    threading.Event().wait(90)
    raise SystemExit("O processo de teste deveria ter sido interrompido")

cycle = int(mode)
with app.state.session_factory() as session:
    operator = session.scalar(select(User).where(User.email == "desktop.operator@local"))
    if operator is None:
        operator = User(
            email="desktop.operator@local",
            display_name="Operador desktop",
            active=True,
            password_hash=hash_password("senha-operador-desktop-validacao"),
            roles=[session.scalar(select(Role).where(Role.code == "user"))],
        )
        session.add(operator)
        session.commit()
    owner = session.scalar(select(User).join(User.roles).where(Role.code == "admin"))
    if owner is None:
        raise RuntimeError("Proprietário descartável não foi criado")
    admin_login = owner.email
    lock_service = AdminLockService(session)
    if lock_service.get() is None:
        lock_service.configure(
            owner.id,
            ADMIN_LOCK_PASSWORD,
            ADMIN_LOCK_PASSWORD,
            15,
        )
    service = CashService(session, "America/Sao_Paulo")
    if service.repository.count() == 0:
        data = CashMovementInput.from_form({"movement_type": "ENTRY", "description": "Teste descartável desktop", "gross_amount": "123,45"}, "America/Sao_Paulo")
        service.create(data, owner.id)
app.state.engine.dispose()

import webview

original_create = webview.create_window
original_start = webview.start
evidence = {
    "login_form_seen": False,
    "remember_checkbox_seen": False,
    "remember_selected": False,
    "remembered_reopen": False,
    "customer_visible": False,
    "cash_movement_visible": False,
    "admin_lock_seen": False,
    "recovery_simple_ui": False,
    "recovery_request_submitted": False,
    "offline_queue_message_seen": False,
    "authorized_reset_ui_seen": False,
    "reset_completed": False,
    "logout_returned_to_login": False,
    "login_after_logout_restart": False,
    "completed": False,
    "routes": [],
    "errors": [],
}
started = time.perf_counter()
window = None
timer = None


def finish() -> None:
    evidence["completed"] = True
    evidence["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 2)
    if timer is not None:
        timer.cancel()
    window.destroy()


def on_loaded():
    try:
        route = window.evaluate_js("location.pathname")
        evidence["routes"].append(route)
        content = str(window.evaluate_js("document.body.innerText") or "")

        if cycle == 1:
            if route == "/login":
                evidence["login_form_seen"] = True
                checkbox_ok = bool(window.evaluate_js(
                    "Boolean(document.querySelector('input[name=remember][value=\"1\"]'))"
                ))
                assert checkbox_ok and "Manter conectado neste dispositivo" in content
                evidence["remember_checkbox_seen"] = True
                window.evaluate_js(
                    f"document.querySelector('[name=email]').value={json.dumps(admin_login)};"
                    f"document.querySelector('[name=password]').value={json.dumps(ADMIN_PASSWORD)};"
                    "document.querySelector('[name=remember]').checked=true;"
                    "document.querySelector('form').submit();"
                )
                evidence["remember_selected"] = True
            elif route == "/clientes/lista":
                assert "Commit preservado" in content
                evidence["customer_visible"] = True
                window.evaluate_js("location.href='/caixa/operacoes'")
            elif route == "/caixa/operacoes":
                assert "Teste descartável desktop" in content and "123,45" in content
                evidence["cash_movement_visible"] = True
                finish()
            else:
                raise AssertionError(f"Rota inesperada no ciclo 1: {route}")
            return

        if cycle == 2:
            if route == "/login":
                raise AssertionError("A sessão lembrada não foi restaurada na reabertura")
            if route == "/clientes/lista":
                evidence["remembered_reopen"] = True
                window.evaluate_js("location.href='/admin/visao-geral'")
            elif route == "/admin/cadeado/desbloquear":
                assert "Administração protegida" in content
                assert "Solicitar recuperação à NexPoint" in content
                assert bool(window.evaluate_js(
                    "Boolean(document.querySelector('input[name=password]'))"
                ))
                evidence["admin_lock_seen"] = True
                window.evaluate_js("location.href='/admin/cadeado/recuperar'")
            elif route == "/admin/cadeado/recuperar":
                lowered = content.casefold()
                for forbidden in (
                    "recovery code", "código de recuperação", "chave offline",
                    "id do chamado", "token temporário", "pedir ajuda à nexa",
                ):
                    assert forbidden not in lowered, forbidden
                assert "Solicitar recuperação à NexPoint" in content
                evidence["recovery_simple_ui"] = True
                if not evidence["recovery_request_submitted"]:
                    evidence["recovery_request_submitted"] = True
                    window.evaluate_js(
                        "document.querySelector('form[action=\"/admin/cadeado/recuperar/suporte\"]').submit()"
                    )
                else:
                    assert "Solicitação salva" in content
                    assert "quando a conexão estiver disponível" in content
                    evidence["offline_queue_message_seen"] = True
                    finish()
            else:
                raise AssertionError(f"Rota inesperada no ciclo 2: {route}")
            return

        if cycle == 3:
            if route == "/login":
                if evidence["reset_completed"]:
                    evidence["logout_returned_to_login"] = True
                    finish()
                    return
                raise AssertionError("A sessão lembrada não chegou ao reset autorizado")
            if route == "/clientes/lista":
                evidence["remembered_reopen"] = True
                window.evaluate_js("location.href='/admin/cadeado/recuperar'")
            elif route == "/admin/cadeado/recuperar":
                assert "Recuperação autorizada" in content
                assert "Nova senha" in content and "Confirmar nova senha" in content
                assert bool(window.evaluate_js(
                    "Boolean(document.querySelector('input[name=new_password]') && "
                    "document.querySelector('input[name=confirmation]'))"
                ))
                lowered = content.casefold()
                for forbidden in ("id do chamado", "token", "recovery code"):
                    assert forbidden not in lowered, forbidden
                evidence["authorized_reset_ui_seen"] = True
                window.evaluate_js(
                    f"document.querySelector('[name=new_password]').value={json.dumps(RESET_ADMIN_LOCK_PASSWORD)};"
                    f"document.querySelector('[name=confirmation]').value={json.dumps(RESET_ADMIN_LOCK_PASSWORD)};"
                    "document.querySelector('form[action=\"/admin/cadeado/recuperar/redefinir\"]').requestSubmit();"
                )
            elif route == "/admin/visao-geral":
                evidence["reset_completed"] = True
                window.evaluate_js(
                    "const f=document.createElement('form');f.method='post';f.action='/logout';"
                    "document.body.appendChild(f);f.submit();"
                )
            else:
                raise AssertionError(f"Rota inesperada no ciclo 3: {route}")
            return

        if cycle == 4:
            assert route == "/login", f"Logout não persistiu na reabertura: {route}"
            assert bool(window.evaluate_js(
                "document.querySelector('[name=password]').value === '' && "
                "document.querySelector('[name=remember]').checked === false"
            ))
            evidence["login_form_seen"] = True
            evidence["remember_checkbox_seen"] = True
            evidence["login_after_logout_restart"] = True
            finish()
            return

        raise AssertionError(f"Ciclo desconhecido: {cycle}")
    except Exception as error:
        evidence["errors"].append(type(error).__name__ + ": " + str(error))
        window.destroy()


def create_window(*args, **kwargs):
    global window
    title = "ERP — validação descartável " + mode
    if args:
        args = (title, *args[1:])
    else:
        kwargs["title"] = title
    window = original_create(*args, **kwargs)
    window.events.loaded += on_loaded
    return window


def start(*args, **kwargs):
    global timer
    kwargs["storage_path"] = str(ROOT / "webview-test-profile")
    kwargs["private_mode"] = False

    def timeout():
        evidence["errors"].append("Timeout da janela de teste")
        window.destroy()

    timer = threading.Timer(40, timeout)
    timer.start()
    original_start(*args, **kwargs)
    timer.cancel()


webview.create_window = create_window
webview.start = start
try:
    run_desktop.run_desktop()
finally:
    (ROOT / f"desktop-{mode}.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8"
    )
if not evidence["completed"]:
    raise SystemExit(1)
