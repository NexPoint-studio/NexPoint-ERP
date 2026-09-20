from __future__ import annotations

from pathlib import Path

from tests.conftest import login


def test_nexa_panel_is_available_only_in_authenticated_layout(client):
    anonymous = client.get('/login').text
    assert 'data-nexa-open' not in anonymous

    login(client, 'admin@local')
    for route in ('/clientes/lista', '/servicos/catalogo', '/caixa/resumo'):
        response = client.get(route)
        assert response.status_code == 200
        assert 'data-nexa-open' in response.text
        assert 'data-nexa-form' in response.text
        assert '/js/nexa.js' in response.text
        assert '/css/nexa.css' in response.text


def test_nexa_client_uses_same_origin_and_text_rendering():
    script = (Path(__file__).resolve().parents[1] / 'static/js/nexa.js').read_text(encoding='utf-8')
    assert "fetch('/nexa/chat'" in script
    assert "credentials: 'same-origin'" in script
    assert 'screen: location.pathname' in script
    assert 'message.textContent' in script
    assert "['Causa provável', risk.probable_cause]" in script
    assert "['Impacto', risk.impact]" in script
    assert "['Recomendação', risk.recommendation]" in script
    assert 'data.available === true' in script
    assert 'Nexa indisponível sem conexão ou configuração ativa.' in script
    assert 'function publicHttpsSource' in script
    assert 'safeSources.length' in script
    assert 'innerHTML' not in script
    assert "url.protocol !== 'https:'" in script
    assert 'window.setInterval' in script
