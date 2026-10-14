from app.core.permissions import ALL_PERMISSIONS
from app.knowledge import ERP_HELP
from app.services.nexa_adapter import public_help


def test_help_permissions_are_real_and_query_is_accent_insensitive():
    assert all(entry[3] in ALL_PERMISSIONS for entry in ERP_HELP)
    permissions = frozenset(ALL_PERMISSIONS)
    assert public_help("preços categorias", permissions) == public_help("precos categorias", permissions)
    assert public_help("preços categorias", permissions)[0]["topic"] == "Catálogo e preços por categoria"


def test_payment_help_preserves_principal_and_actual_partial_flow():
    result = public_help("pagamentos", frozenset({"notes.view"}))
    assert result[0]["topic"] == "Pagamentos"
    assert "parte do saldo" in result[0]["summary"]
    assert "principal quita" in result[0]["summary"]


def test_configuration_help_stays_permission_scoped_and_bounded():
    query = "taxas percentual fixo operadora"
    assert "Configurar taxas" not in {r["topic"] for r in public_help(query, frozenset({"notes.view"}))}
    assert public_help(query, frozenset({"finance.config.manage"}))[0]["topic"] == "Configurar taxas"
    assert public_help(query, frozenset()) == []
    assert len(public_help("", frozenset(ALL_PERMISSIONS))) <= 5


def test_unknown_topic_does_not_invent_guidance():
    assert public_help("xyzinexistente", frozenset(ALL_PERMISSIONS)) == []
