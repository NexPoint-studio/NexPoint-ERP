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
from app.models import Customer, User
from app.services.cash import CashService
from app.services.cash_validation import CashMovementInput
from sqlalchemy import select
import run_desktop

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

with app.state.session_factory() as session:
    service = CashService(session, "America/Sao_Paulo")
    if service.repository.count() == 0:
        actor = session.scalar(select(User.id))
        data = CashMovementInput.from_form({"movement_type": "ENTRY", "description": "Teste descartável desktop", "gross_amount": "123,45"}, "America/Sao_Paulo")
        service.create(data, actor)
app.state.engine.dispose()

import webview
original_create = webview.create_window
original_start = webview.start
evidence = {"login_form_seen": False, "cash_balance": None, "completed": False, "errors": []}
started = time.perf_counter()
window = None
timer = None

def on_loaded():
    try:
        route = window.evaluate_js("location.pathname")
        if route == "/login":
            evidence["login_form_seen"] = True
            window.evaluate_js("document.querySelector('[name=email]').value='adm'; document.querySelector('[name=password]').value='adm'; document.querySelector('form').requestSubmit();")
        elif route == "/clientes/lista":
            window.load_url("http://127.0.0.1:8877/caixa/resumo")
        elif route == "/caixa/resumo":
            value = window.evaluate_js("document.querySelector('.primary-banner__value').innerText")
            assert "123,45" in value, value
            evidence.update(cash_balance="123.45", completed=True, startup_to_summary_ms=round((time.perf_counter()-started)*1000, 2))
            timer.cancel()
            window.destroy()
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
    timer = threading.Timer(35, timeout)
    timer.start()
    original_start(*args, **kwargs)
    timer.cancel()

webview.create_window = create_window
webview.start = start
try:
    run_desktop.run_desktop()
finally:
    (ROOT / f"desktop-{mode}.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
if not evidence["completed"]:
    raise SystemExit(1)
