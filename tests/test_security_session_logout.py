"""SD-001: persistent per-session revocation, independent of signed-cookie age."""
import base64
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import re
import secrets
import sqlite3

from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner
import pytest

from control_center.web import create_control_center_app, SESSION_COOKIE
from control_center.config import ControlCenterSettings
from control_center.local_repository import LocalControlCenterRepository
from control_center.domain import PlatformUser


@pytest.fixture
def fixture(tmp_path):
    secret, password = secrets.token_urlsafe(48), secrets.token_urlsafe(24)
    path = tmp_path / 'control.sqlite3'
    initial = LocalControlCenterRepository(path)
    initial.save_platform_user(PlatformUser(id='qa-admin', username='qa-admin', display_name='QA'),password=password)
    initial.close()
    settings = ControlCenterSettings(host='127.0.0.1',port=8770,database_path=path,
        session_secret=secret,admin_username='',admin_password='',storage='supabase')
    def build():
        # Inject the SQLite test repository without the LOCAL credential bootstrap,
        # which intentionally resets its configured development password on startup.
        return create_control_center_app(settings=settings, repository=LocalControlCenterRepository(path), nexa_secret='')
    app = build()
    yield app, password, secret, path, build
    app.state.control_repository.close()


def token(client, login=False):
    text = client.get('/login' if login else '/').text
    pattern = r'name="_csrf" value="([^"]+)"' if login else r'name="csrf-token" content="([^"]+)"'
    return re.search(pattern, text).group(1)


def authenticated(app, password):
    client = TestClient(app, follow_redirects=False)
    assert client.post('/login', headers={'Origin':'http://testserver'}, data={
        'username':'qa-admin','password':password,'_csrf':token(client,True)}).status_code == 303
    assert client.get('/').status_code == 200
    return client


def copied(app, cookie):
    client = TestClient(app, follow_redirects=False)
    client.cookies.set(SESSION_COOKIE, cookie)
    return client


def logout(client):
    return client.post('/logout', headers={'Origin':'http://testserver'}, data={'_csrf':token(client)})


def test_logout_revokes_copied_cookie_across_instances_and_restarts(fixture):
    app, password, secret, path, build = fixture
    one, two = authenticated(app,password), authenticated(app,password)
    captured = one.cookies.get(SESSION_COOKIE)
    other_app = build()
    assert copied(other_app,captured).get('/').status_code == 200
    assert logout(one).status_code == 303
    assert copied(app,captured).get('/').status_code == 303
    assert copied(other_app,captured).get('/').status_code == 303
    assert two.get('/').status_code == 200
    assert copied(build(),captured).get('/').status_code == 303


def test_expired_server_session_cannot_be_extended_by_cookie_resigning(fixture):
    app,password,secret,path,_ = fixture
    client = authenticated(app,password)
    value=client.cookies.get(SESSION_COOKIE)
    with sqlite3.connect(path) as db:
        db.execute('UPDATE platform_sessions SET created_at=?,expires_at=?', (
            (datetime.now(timezone.utc)-timedelta(days=2)).isoformat(),
            (datetime.now(timezone.utc)-timedelta(days=1)).isoformat()))
    assert copied(app,value).get('/').status_code == 303


def test_legacy_cookie_without_server_session_requires_login(fixture):
    app,password,secret,_,_=fixture
    client=authenticated(app,password)
    signer=TimestampSigner(secret)
    payload=json.loads(base64.b64decode(signer.unsign(client.cookies.get(SESSION_COOKIE))))
    payload.pop('platform_session_token',None)
    legacy=signer.sign(base64.b64encode(json.dumps(payload).encode())).decode()
    assert copied(app,legacy).get('/').status_code == 303


def test_invalid_csrf_does_not_revoke_session(fixture):
    app,password,*_=fixture
    client=authenticated(app,password)
    cookie=client.cookies.get(SESSION_COOKIE)
    assert client.post('/logout',headers={'Origin':'http://testserver'},data={'_csrf':'invalid'}).status_code==403
    assert copied(app,cookie).get('/').status_code==200


@pytest.mark.parametrize('change',['password','inactive','role'])
def test_existing_credential_and_actor_checks_preserved(fixture,change):
    app,password,*_=fixture
    client=authenticated(app,password)
    repo=app.state.control_repository
    user=repo.get_platform_user_by_username('qa-admin')
    if change=='password':repo.save_platform_user(user,password=secrets.token_urlsafe(24))
    elif change=='inactive':repo.save_platform_user(replace(user,active=False))
    else:repo.save_platform_user(replace(user,role='nexpoint_control_admin',authorized_tenant_ids=()))
    response=client.get('/')
    assert response.status_code==(200 if change=='role' else 303)
    if change=='role':assert client.get('/empresas/missing').status_code==404


@pytest.mark.parametrize('operation',['create_platform_session','platform_session_active','revoke_platform_session'])
def test_session_storage_failure_fails_closed_without_details(fixture,monkeypatch,operation):
    app,password,*_=fixture
    client=authenticated(app,password)
    csrf=token(client)
    repo=app.state.control_repository
    def fail(*args,**kwargs):raise RuntimeError('PRIVATE_BACKEND_DETAILS')
    monkeypatch.setattr(repo,operation,fail)
    if operation=='create_platform_session':
        client=TestClient(app,follow_redirects=False)
        response=client.post('/login',headers={'Origin':'http://testserver'},data={
            'username':'qa-admin','password':password,'_csrf':token(client,True)})
    elif operation=='revoke_platform_session':
        response=client.post('/logout',headers={'Origin':'http://testserver'},data={'_csrf':csrf})
    else:response=client.get('/')
    assert response.status_code==503
    assert 'PRIVATE_BACKEND_DETAILS' not in response.text
