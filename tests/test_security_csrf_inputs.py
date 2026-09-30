"""SD-007: malformed synthetic CSRF input must be denied without exceptions."""
from starlette.requests import Request
from fastapi import HTTPException
import pytest

from control_center.web import _require_csrf, CSRF_SESSION_KEY
from tests.test_security_session_logout import fixture, authenticated, token


@pytest.mark.parametrize('candidate', [None, '', 'wrong-ascii', '\u00e9', 'e\u0301',
                                      '\U0001f512', '\udcff', 'x' * 10000],
                         ids=['null', 'empty', 'ascii', 'unicode', 'combining', 'emoji', 'surrogate', 'long'])
def test_invalid_csrf_is_controlled(candidate):
    request = Request({'type': 'http', 'headers': [], 'session': {CSRF_SESSION_KEY: 'a' * 43}})
    with pytest.raises(HTTPException) as error:
        _require_csrf(request, candidate)
    assert error.value.status_code == 403


@pytest.mark.parametrize('value', ['\u00e9', '\u2603', 'e\u0301', '\U0001f512', '', 'x' * 10000, None],
                         ids=['unicode', 'snowman', 'combining', 'emoji', 'empty', 'long', 'missing'])
def test_malformed_csrf_cannot_logout_or_raise_500(fixture, value):
    app, password, *_ = fixture
    client = authenticated(app, password)
    data = {} if value is None else {'_csrf': value}
    response = client.post('/logout', headers={'Origin': 'http://testserver'}, data=data)
    assert response.status_code in (403, 422)
    assert 'Traceback' not in response.text
    assert client.get('/').status_code == 200
    response = client.post('/logout', headers={'Origin': 'http://testserver'}, data={'_csrf': token(client)})
    assert response.status_code == 303


def test_invalid_encoding_is_controlled(fixture):
    app, password, *_ = fixture
    client = authenticated(app, password)
    for body in (b'_csrf=%FF', b'_csrf=%C3%28', b'_csrf=%ZZ'):
        response = client.post('/logout', content=body, headers={
            'Origin': 'http://testserver', 'Content-Type': 'application/x-www-form-urlencoded'})
        assert response.status_code in (400, 403, 422)
        assert client.get('/').status_code == 200


def test_csrf_still_uses_constant_time_comparison(monkeypatch):
    import control_center.web as web
    calls = []
    original = web.secrets.compare_digest
    def observe(a, b):
        calls.append((len(a), len(b)))
        return original(a, b)
    monkeypatch.setattr(web.secrets, 'compare_digest', observe)
    request = Request({'type': 'http', 'headers': [], 'session': {CSRF_SESSION_KEY: 'a' * 43}})
    _require_csrf(request, 'a' * 43)
    with pytest.raises(HTTPException):
        _require_csrf(request, 'b' * 43)
    assert len(calls) == 2
