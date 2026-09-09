from __future__ import annotations

from pathlib import Path

from sqlalchemy import inspect, select

from app.core import config as config_module
from app.core.config import Settings, get_settings
from app.core.modules import MODULE_BY_ID
from app.models import AuditEvent, User
from run_desktop import wait_for_server
from tests.conftest import TEST_CREDENTIALS, login


def test_application_and_fastapi_start(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "erp-template"}


def test_external_fastapi_documentation_assets_are_disabled(client):
    login(client, "admin@local")
    for path in ("/docs", "/redoc", "/openapi.json"):
        response = client.get(path)
        assert response.status_code == 404


def test_root_redirects_to_login_when_anonymous(client):
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_admin_and_user_login(client):
    for email in TEST_CREDENTIALS:
        response = login(client, email)
        assert response.status_code == 303
        assert response.headers["location"] == "/clientes/lista"
        client.post("/logout")


def test_invalid_login_is_rejected(client):
    response = login(client, "admin@local", "senha-incorreta")
    assert response.status_code == 401
    assert "Usuário ou senha inválidos" in response.text


def test_logout_clears_local_session(client):
    login(client, "usuario@local")
    assert client.post("/logout", follow_redirects=False).status_code == 303
    assert client.get("/caixa/resumo", follow_redirects=False).status_code == 303


def test_registry_contains_only_four_approved_modules():
    assert list(MODULE_BY_ID) == ["cash", "customers", "services", "admin"]
    assert all(module.status for module in MODULE_BY_ID.values())


def test_expected_contextual_tabs():
    assert [tab.name for tab in MODULE_BY_ID["cash"].tabs] == ["Operações", "Novo lançamento"]
    assert [tab.name for tab in MODULE_BY_ID["customers"].tabs] == ["Lista", "Novo cliente", "Histórico"]
    assert [tab.name for tab in MODULE_BY_ID["services"].tabs] == [
        "Catálogo", "Nova Nota", "Notas de Serviço"
    ]
    assert [tab.name for tab in MODULE_BY_ID["admin"].tabs] == [
        "Visão geral", "Serviços", "Financeiro", "Pagamentos e taxas",
        "Usuários e permissões", "Empresa", "Suporte", "Auditoria", "Sistema",
    ]


def test_admin_can_open_every_screen(client):
    login(client, "admin@local")
    for module in MODULE_BY_ID.values():
        for tab in module.tabs:
            assert client.get(tab.path).status_code == 200, tab.path


def test_sidebar_is_neutral_and_has_only_allowed_modules(client):
    login(client, "admin@local")
    text = client.get("/caixa/resumo").text
    for label in ("Caixa", "Clientes", "Serviços", "Administração", "SEU LOGO"):
        assert label in text
    for forbidden in ("Início", "Ordens", "Produção", "Corporativo", "Entregas"):
        assert f'>{forbidden}<' not in text


def test_contextual_tabs_change_with_module(client):
    login(client, "admin@local")
    cash = client.get("/caixa/operacoes").text
    customers = client.get("/clientes/lista").text
    services = client.get("/servicos/catalogo").text
    assert "Novo lançamento" in cash and "Novo cliente" not in cash
    assert "Novo cliente" in customers and "Novo serviço" not in customers
    assert "Nova Nota" in services and "Novo serviço" not in services


def test_user_cannot_access_administration(client):
    login(client, "usuario@local")
    assert client.get("/admin/usuarios").status_code == 403
    assert ">Administração<" not in client.get("/caixa/operacoes").text


def test_user_can_access_business_placeholders(client):
    login(client, "usuario@local")
    for path in ("/caixa/operacoes", "/clientes/lista", "/servicos/catalogo"):
        assert client.get(path).status_code == 200


def test_sqlite_contains_infrastructure_and_domain_tables(app):
    assert app.state.engine.url.get_backend_name() == "sqlite"
    with app.state.engine.connect() as connection:
        tables = set(inspect(connection).get_table_names())
    assert tables == {
        "audit_events", "cash_categories", "cash_movements", "cash_payment_methods",
        "customer_activities", "customer_addresses", "customers",
        "feature_flags", "permissions", "role_permissions", "roles",
        "schema_migrations", "service_categories", "service_prices", "services",
        "billing_units", "service_notes", "service_note_items", "service_note_events",
        "settings", "user_roles", "users",
        "payments", "payment_terminals", "payment_fee_rules", "support_grants",
    }


def test_database_has_only_two_generic_local_users(app):
    with app.state.session_factory() as session:
        users = list(session.scalars(select(User)))
    assert {user.email for user in users} == {"admin@local", "usuario@local"}
    assert all(user.password_hash.startswith("scrypt$") for user in users)


def test_authentication_and_admin_navigation_are_audited(client, app):
    login(client, "admin@local")
    client.get("/admin/usuarios")
    client.post("/logout")
    with app.state.session_factory() as session:
        actions = set(session.scalars(select(AuditEvent.action)))
    assert {"auth.login", "admin.navigate", "auth.logout"} <= actions


def test_settings_are_strictly_local():
    settings = Settings()
    assert settings.host == "127.0.0.1"
    assert settings.database_url == ""
    assert settings.logo_path.startswith("/static/")


def test_settings_uses_local_sqlite_and_ensures_data_directory(monkeypatch, tmp_path):
    monkeypatch.setattr(config_module, "ROOT_DIR", tmp_path)
    monkeypatch.setenv("ERP_SESSION_SECRET", "s" * 40)
    settings = get_settings()
    assert settings.database_url.startswith("sqlite+pysqlite:///")
    assert settings.database_url.endswith("/data/erp.sqlite3")
    assert (tmp_path / "data").is_dir()


def test_no_cloud_package_or_external_runtime_reference():
    root = Path(__file__).resolve().parents[1]
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8").lower()
    for package in ("supabase", "firebase", "cloudflare", "psycopg", "postgres"):
        assert package not in pyproject
    runtime_files = [
        *root.joinpath("app").rglob("*.py"),
        *root.joinpath("templates").rglob("*.html"),
        *root.joinpath("static", "js").rglob("*.js"),
        root / "run_dev.py", root / "run_desktop.py",
    ]
    content = "\n".join(path.read_text(encoding="utf-8").lower() for path in runtime_files)
    for forbidden in ("supabase", "firebase", "cloudflare", "0.0.0.0", "https://"):
        assert forbidden not in content
    http_references = [line for line in content.splitlines() if "http://" in line]
    assert http_references and all("127.0.0.1" in line for line in http_references)


def test_project_source_has_no_absolute_dependency_on_original_projects():
    root = Path(__file__).resolve().parents[1]
    files = [*root.joinpath("app").rglob("*.py"), *root.joinpath("templates").rglob("*.html"), root / "pyproject.toml"]
    content = "\n".join(path.read_text(encoding="utf-8").lower() for path in files)
    assert "nexstudio" not in content
    assert "nil_lav" not in content
    assert "nil lav" not in content


def test_pywebview_runtime_is_present_and_local():
    import webview
    assert callable(webview.create_window)
    assert callable(wait_for_server)


# REVIEW: a abertura real do pywebview é validada ao final da tarefa.
