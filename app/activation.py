"""Loopback-only first-run screen, replaced by the ERP after safe enrollment."""
from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from html import escape
import secrets
from urllib.parse import parse_qs, urlencode

from starlette.responses import HTMLResponse, PlainTextResponse, RedirectResponse

from app.services.installation_activation import ActivationError


_SCRIPT = """document.querySelector('form').addEventListener('submit', () => {
  const button = document.querySelector('button');
  button.disabled = true;
  button.textContent = 'Configurando este computador pela primeira vez...';
  document.querySelector('#progress').textContent = 'Aguarde. Mantenha esta janela aberta.';
});"""


class ActivationGateway:
    def __init__(self, service, application_factory):
        self.service, self.application_factory = service, application_factory
        self.application = None
        self.csrf = secrets.token_urlsafe(32)
        self.lock = asyncio.Lock()
        self.stack = AsyncExitStack()

    def page(self, error="", status=200):
        company = escape(self.service.profile.company_name)
        username = escape(self.service.profile.username, quote=True)
        alert = f'<p role="alert" class="error">{escape(error)}</p>' if error else ""
        return HTMLResponse(f"""<!doctype html><html lang="pt-BR"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Primeiro acesso — NexPoint ERP</title><link rel="icon" href="data:,">
<script defer src="/activation.js"></script><style>
*{{box-sizing:border-box}}body{{margin:0;background:#f3f6fb;color:#17243b;font:16px 'Segoe UI',sans-serif;min-height:100vh;display:grid;place-items:center;padding:24px}}
main{{background:white;width:100%;max-width:490px;border:1px solid #dce3ee;border-radius:18px;padding:36px;box-shadow:0 18px 60px #17243b12}}
.brand{{font-size:14px;font-weight:700;color:#365ca8;letter-spacing:.06em}}h1{{font-size:26px;margin-bottom:8px}}p{{line-height:1.5;color:#56637a}}label{{display:block;font-weight:600;margin:20px 0 8px}}input:not([type=checkbox]){{width:100%;padding:13px;border:1px solid #bfcadd;border-radius:8px;font:inherit}}
.remember{{font-size:14px;font-weight:400;display:flex;gap:8px;align-items:center}}button{{width:100%;padding:14px;border:0;border-radius:8px;background:#315fbe;color:white;font:600 16px 'Segoe UI',sans-serif;cursor:pointer;margin-top:20px}}button:disabled{{background:#647b9e;cursor:wait}}.error{{background:#fff0ed;color:#96352c;padding:14px;border-radius:8px}}small{{display:block;color:#66748b;line-height:1.5;margin-top:16px}}#progress{{font-size:14px;min-height:24px}}
</style></head><body><main><div class="brand">NEXPOINT ERP</div><h1>{company}</h1>
<p>Bem-vinda! Entre para configurar este computador e começar a usar o sistema.</p>{alert}
<form action="/activate" method="post"><input type="hidden" name="csrf" value="{self.csrf}">
<label for="email">Usuário</label><input id="email" name="email" value="{username}" autocomplete="username" maxlength="160" required>
<label for="password">Senha</label><input id="password" name="password" type="password" autocomplete="current-password" minlength="8" maxlength="256" autofocus required>
<label class="remember"><input type="checkbox" name="remember" value="1"> Manter conectado neste computador</label>
<button type="submit">Entrar e configurar</button><p id="progress" role="status" aria-live="polite"></p>
</form><small>A primeira configuração precisa de internet. Depois, você poderá trabalhar mesmo com quedas de conexão.</small>
<small>Precisa de ajuda? Contate a NexPoint. Não compartilhe sua senha.</small></main></body></html>""", status_code=status)

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    await self.stack.aclose()
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        if scope["type"] != "http":
            return
        if self.application is not None:
            await self.application(scope, receive, send)
            return
        headers = {k.lower(): v for k, v in scope.get("headers", [])}
        port = self.service.settings.port
        expected_host = f"127.0.0.1:{port}".encode()
        expected_origin = f"http://127.0.0.1:{port}".encode()
        if headers.get(b"host") != expected_host:
            await PlainTextResponse("Solicitação local inválida.", status_code=400)(scope, receive, send)
            return

        async def secure_send(message):
            if message["type"] == "http.response.start":
                message["headers"] = list(message.get("headers", [])) + [
                    (b"cache-control", b"no-store"), (b"x-content-type-options", b"nosniff"),
                    (b"x-frame-options", b"DENY"), (b"referrer-policy", b"same-origin"),
                    (b"content-security-policy", b"default-src 'none'; script-src 'self'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'; img-src data:"),
                ]
            await send(message)

        path, method = scope["path"], scope["method"]
        if method == "GET":
            if path in {"/", "/login"}:
                response = self.page()
            elif path == "/activation.js":
                response = PlainTextResponse(_SCRIPT, media_type="application/javascript")
            else:
                response = PlainTextResponse("Não encontrado.", status_code=404)
            await response(scope, receive, secure_send)
            return
        if method != "POST" or path != "/activate":
            await PlainTextResponse("Solicitação inválida.", status_code=405)(scope, receive, secure_send)
            return
        if headers.get(b"origin") != expected_origin or headers.get(b"content-type", b"").split(b";")[0] != b"application/x-www-form-urlencoded":
            await PlainTextResponse("Solicitação local inválida.", status_code=403)(scope, receive, secure_send)
            return
        raw = bytearray()
        while True:
            part = await receive()
            if part["type"] != "http.request":
                return
            raw.extend(part.get("body", b""))
            if len(raw) > 8192:
                await PlainTextResponse("Solicitação inválida.", status_code=413)(scope, receive, secure_send)
                return
            if not part.get("more_body"):
                break
        try:
            fields = parse_qs(raw.decode("utf-8"), keep_blank_values=True, max_num_fields=4)
            if any(len(value) != 1 for value in fields.values()) or set(fields) - {"csrf", "email", "password", "remember"}:
                raise ValueError()
            values = {k: v[0] for k, v in fields.items()}
            if not secrets.compare_digest(values.get("csrf", ""), self.csrf):
                raise ValueError()
        except (ValueError, UnicodeError, TypeError):
            await PlainTextResponse("Solicitação local inválida.", status_code=403)(scope, receive, secure_send)
            return
        async with self.lock:
            if self.application is not None:
                await RedirectResponse("/login", status_code=303)(scope, receive, secure_send)
                return
            try:
                await asyncio.to_thread(self.service.activate, values.get("email", ""), values.get("password", ""))
                app = await asyncio.to_thread(self.application_factory)
                await self.stack.enter_async_context(app.router.lifespan_context(app))
                self.application = app
            except ActivationError as error:
                await self.page(str(error), status=400)(scope, receive, secure_send)
                return
            except Exception:
                await self.page("A configuração foi preservada, mas não foi possível abrir o sistema. Feche e abra novamente; se persistir, contate a NexPoint.", status=503)(scope, receive, secure_send)
                return
            # The real login route issues/revokes sessions exactly as on later
            # starts. The password is never placed in HTML, URLs or JS storage.
            login_body = urlencode({k: values.get(k, "") for k in ("email", "password", "remember")}).encode()
            login_scope = dict(scope, path="/login", raw_path=b"/login", query_string=b"")
            login_scope["headers"] = [(k, v) for k, v in scope["headers"] if k.lower() not in {b"content-length", b"cookie"}]
            login_scope["headers"].append((b"content-length", str(len(login_body)).encode()))
            delivered = False

            async def login_receive():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {"type": "http.request", "body": login_body, "more_body": False}
                return await receive()

            await self.application(login_scope, login_receive, send)
