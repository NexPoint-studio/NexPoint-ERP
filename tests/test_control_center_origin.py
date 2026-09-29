"""Browser-origin and HTTPS proxy regressions; no production data or credentials."""

from dataclasses import replace
import secrets

import pytest
from fastapi.testclient import TestClient
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from control_center.config import get_control_center_settings
from control_center.domain import PlatformUser
from control_center.local_repository import LocalControlCenterRepository
from control_center.web import create_control_center_app, SESSION_COOKIE
from tests.test_prod_control_center import production_environment  # noqa: F401


HOST = "nexpoint-erp-control-center.onrender.com"
ORIGIN = f"https://{HOST}"
USERNAME = "origin-test-admin"
PASSWORD = "CANARY-origin-test-password-only"


@pytest.fixture
def render_login(production_environment, tmp_path, monkeypatch):
    production_environment.setenv("RENDER", "true")
    production_environment.setenv("CONTROL_CENTER_TRUST_PROXY", "1")
    production_environment.setenv("CONTROL_CENTER_FORWARDED_ALLOW_IPS", "*")
    settings = replace(
        get_control_center_settings(), public_origin=ORIGIN, allowed_hosts=(HOST,),
    )
    repository = LocalControlCenterRepository(tmp_path / "isolated-control.sqlite3")
    repository.save_platform_user(PlatformUser(
        id="origin-admin", username=USERNAME, display_name="Origin test",
        role="platform_admin",
    ), password=PASSWORD)
    app = create_control_center_app(settings=settings, repository=repository)
    authenticated = []
    original = repository.authenticate_platform_user

    def authenticate(username, password):
        authenticated.append(True)  # No credentials in diagnostics.
        return original(username, password)

    monkeypatch.setattr(repository, "authenticate_platform_user", authenticate)
    scopes = []

    async def capture(scope, receive, send):
        if scope["type"] == "http":
            scopes.append(scope["scheme"])
        await app(scope, receive, send)

    proxy = ProxyHeadersMiddleware(capture, trusted_hosts=list(settings.forwarded_allow_ips))

    async def ingress(scope, receive, send):
        # Render terminates TLS, then forwards HTTP to Uvicorn. The browser URL
        # stays HTTPS, so Secure cookies behave as in production.
        if scope["type"] == "http":
            scope = dict(scope, scheme="http", server=("internal-container", 10000))
        await proxy(scope, receive, send)

    headers = {"Host": HOST, "X-Forwarded-Proto": "https", "X-Forwarded-Host": HOST}
    with TestClient(ingress, base_url=ORIGIN, headers=headers, follow_redirects=False) as client:
        yield client, authenticated, scopes, settings, app
    repository.close()


def login_form(client):
    page = client.get("/login")
    assert page.status_code == 200
    assert page.headers["referrer-policy"] == "same-origin"
    cookie = page.headers["set-cookie"].lower()
    assert "secure" in cookie and "httponly" in cookie and "samesite=strict" in cookie
    token = page.text.split('name="_csrf" value="', 1)[1].split('"', 1)[0]
    return {"username": USERNAME, "password": PASSWORD, "_csrf": token}


@pytest.mark.parametrize("source", [
    {"Origin": ORIGIN},
    {"Origin": f"https://{HOST}:443"},
    {"Origin": f"HTTPS://{HOST.upper()}"},
    {"Referer": f"{ORIGIN}/login?private-query=not-logged"},
])
def test_render_login_accepts_canonical_origin_and_creates_session(render_login, source):
    client, authenticated, scopes, _, _ = render_login
    data = login_form(client)
    before = client.cookies.get(SESSION_COOKIE)
    response = client.post("/login", headers=source, data=data)
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert authenticated == [True]
    assert set(scopes) == {"https"}
    assert client.cookies.get(SESSION_COOKIE) != before
    assert client.get("/login").status_code == 303  # The stored session authenticates.
    assert client.get("/").status_code == 200
    # Pre-login CSRF is no longer valid after the successful session rotation.
    assert client.post("/logout", headers={"Origin": ORIGIN}, data={"_csrf": data["_csrf"]}).status_code == 403


@pytest.mark.parametrize("origin", [
    "http://evil.example", "https://evil.example",
    f"{ORIGIN}.evil.example", f"https://evil-{HOST}",
    f"http://{HOST}", f"{ORIGIN}:80", f"{ORIGIN}:444", f"{ORIGIN}:0",
    f"https://user:password@{HOST}", f"https://{HOST}@evil.example",
    "null", "", "https://", "https://[invalid", f"{ORIGIN}:invalid",
    f"{ORIGIN}:", f"{ORIGIN}/login", f"{ORIGIN}?query", f"{ORIGIN}#fragment",
    f"{ORIGIN}, https://evil.example", f" {ORIGIN}",
    f"https://{HOST}\\@evil.example", f"https://{HOST}%2eevil.example",
])
def test_bad_origin_never_falls_back_to_referer_or_authenticates(render_login, origin):
    client, authenticated, _, _, _ = render_login
    response = client.post("/login", data=login_form(client), headers={
        "Origin": origin, "Referer": f"{ORIGIN}/login",
    })
    assert response.status_code == 403
    assert authenticated == []
    assert "Recarregue a página" in response.text
    assert 'type="password"' in response.text
    assert "Ambiente PROD" in response.text
    assert response.headers["referrer-policy"] == "same-origin"
    assert response.headers["cache-control"] == "no-store"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert client.get("/").status_code == 303


@pytest.mark.parametrize("headers", [
    {}, {"Referer": "https://evil.example/login"},
    [("Origin", ORIGIN), ("Origin", "https://evil.example")],
    [("Referer", ORIGIN), ("Referer", "https://evil.example")],
])
def test_missing_or_ambiguous_origin_is_rejected_in_prod(render_login, headers):
    client, authenticated, _, _, _ = render_login
    assert client.post("/login", data=login_form(client), headers=headers).status_code == 403
    assert authenticated == []


@pytest.mark.parametrize("csrf", [None, "wrong-csrf-token"])
def test_invalid_csrf_retains_login_ui_without_authentication(render_login, csrf):
    client, authenticated, _, _, _ = render_login
    data = login_form(client)
    data.pop("_csrf")
    if csrf is not None:
        data["_csrf"] = csrf
    response = client.post("/login", headers={"Origin": ORIGIN}, data=data)
    assert response.status_code == 403
    assert authenticated == []
    assert "Recarregue a página" in response.text
    assert client.get("/").status_code == 303


def test_wrong_password_reaches_authentication_and_returns_normal_error(render_login):
    client, authenticated, _, _, _ = render_login
    data = login_form(client)
    data["password"] = "CANARY-incorrect-password"
    response = client.post("/login", headers={"Origin": ORIGIN}, data=data)
    assert response.status_code == 401
    assert "Usuário interno ou senha inválidos" in response.text
    assert authenticated == [True]
    assert client.get("/").status_code == 303


def test_forwarded_host_cannot_replace_authoritative_origin(render_login):
    client, authenticated, _, _, _ = render_login
    data = login_form(client)
    response = client.post("/login", data=data, headers={
        "Origin": "https://evil.example", "X-Forwarded-Host": "evil.example",
    })
    assert response.status_code == 403
    assert authenticated == []
    response = client.post("/login", data=data, headers={
        "Origin": ORIGIN, "Host": "evil.example", "X-Forwarded-Host": HOST,
    })
    assert response.status_code == 400  # TrustedHost cannot be bypassed by forwarded host.
    assert authenticated == []


@pytest.mark.parametrize("trusted", [False, True])
def test_untrusted_forwarded_proto_and_real_http_fail_closed(render_login, trusted):
    _, authenticated, _, _, app = render_login
    proxy = ProxyHeadersMiddleware(app, trusted_hosts="*" if trusted else [])
    with TestClient(proxy, base_url=f"http://{HOST}") as client:
        response = client.post("/login", headers={
            "Origin": ORIGIN, "X-Forwarded-Proto": "http" if trusted else "https",
            "X-Forwarded-Host": HOST,
        })
    assert response.status_code == 400
    assert authenticated == []


def test_login_guards_do_not_log_or_echo_secrets(render_login, caplog, capsys):
    client, authenticated, _, settings, _ = render_login
    data = login_form(client)
    marker = secrets.token_urlsafe(40)
    cookie = client.cookies.get(SESSION_COOKIE)
    data["password"] = marker
    response = client.post("/login", data=data, headers={
        "Origin": "null", "Authorization": f"Bearer {marker}",
        "Referer": f"{ORIGIN}/login?secret={marker}",
    })
    assert response.status_code == 403 and authenticated == []
    captured = capsys.readouterr()
    logs = caplog.text + captured.out + captured.err
    for value in (marker, cookie, data["_csrf"], settings.session_secret,
                  settings.supabase_service_role_key, settings.nexa_bridge_secret):
        assert value not in logs
    assert marker not in response.text
