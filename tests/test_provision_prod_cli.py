from __future__ import annotations

from dataclasses import replace
from io import StringIO
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from app.core.installation_identity import InstallationCredentialStore
from control_center.security import verify_password
from scripts import provision_prod


PROJECT_REF = provision_prod.PRODUCTION_SUPABASE_PROJECT_REF
SUPABASE_URL = f"https://{PROJECT_REF}.supabase.co"
SERVICE_ROLE = "sb_secret_" + "R" * 40
ADMIN_PASSWORD = "Senha-admin-muito-segura-2026"
INSTALLATION_SECRET = "S" * 64
TENANT_KEY = "tenant_prod_001"
INSTALLATION_KEY = "installation_prod_001"
TENANT_UUID = "11111111-1111-4111-8111-111111111111"
INSTALLATION_UUID = "22222222-2222-4222-8222-222222222222"
ADMIN_UUID = "33333333-3333-4333-8333-333333333333"


class ReversibleTestProtector:
    def protect(self, plaintext: bytes) -> bytes:
        return bytes(value ^ 0xA5 for value in plaintext)

    def unprotect(self, ciphertext: bytes) -> bytes:
        return bytes(value ^ 0xA5 for value in ciphertext)


def _store_factory(path: Path) -> InstallationCredentialStore:
    return InstallationCredentialStore(path, protector=ReversibleTestProtector())


class FakeSupabase:
    def __init__(self) -> None:
        self.admin = None
        self.tenant = None
        self.installation = None
        self.credential = None
        self.rpc_failure = None
        self.probe_status = 200
        self.probe_payload = {"users": []}
        self.admin_select_status = 200
        self.calls = []

    @staticmethod
    def _json(value, status=200):
        return (
            status,
            {"Content-Type": "application/json; charset=utf-8"},
            json.dumps(value, separators=(",", ":")).encode("utf-8"),
        )

    def transport(self, method, url, headers, body, timeout):
        parsed = urlsplit(url)
        query = parse_qs(parsed.query)
        payload = json.loads(body.decode("utf-8")) if body is not None else None
        self.calls.append(
            {
                "method": method,
                "path": parsed.path,
                "query": query,
                "headers": dict(headers),
                "payload": payload,
                "timeout": timeout,
            }
        )
        assert f"{parsed.scheme}://{parsed.netloc}" == SUPABASE_URL
        assert headers["apikey"] == SERVICE_ROLE
        assert "Authorization" not in headers

        if method == "GET" and parsed.path == "/auth/v1/admin/users":
            assert query == {"page": ["1"], "per_page": ["1"]}
            return self._json(self.probe_payload, status=self.probe_status)
        if method == "GET" and parsed.path == "/rest/v1/np_platform_users":
            return self._json(
                [self.admin] if self.admin else [], status=self.admin_select_status
            )
        if method == "GET" and parsed.path == "/rest/v1/np_tenants":
            return self._json([self.tenant] if self.tenant else [])
        if method == "PATCH" and parsed.path == "/rest/v1/np_tenants":
            assert self.tenant is not None
            assert query.get("id") == [f"eq.{TENANT_UUID}"]
            assert payload == {"billing_exempt": True}
            self.tenant["billing_exempt"] = True
            return self._json([self.tenant])
        if method == "GET" and parsed.path == "/rest/v1/np_installations":
            return self._json([self.installation] if self.installation else [])
        if (
            method == "GET"
            and parsed.path == "/rest/v1/np_installation_credentials"
        ):
            if self.credential is None:
                return self._json([])
            return self._json(
                [{
                    "installation_id": INSTALLATION_UUID,
                    "key_id": self.credential[0],
                    "kind": "hmac_sha256",
                    "secret_digest": "\\x" + self.credential[1],
                    "revoked_at": None,
                    "expires_at": None,
                }]
            )
        if method == "POST" and parsed.path == "/rest/v1/np_platform_users":
            assert self.admin is None
            self.admin = {"id": ADMIN_UUID, **payload}
            return self._json([self.admin], status=201)
        if (
            method == "POST"
            and parsed.path == "/rest/v1/rpc/np_admin_provision_installation"
        ):
            if self.rpc_failure:
                return self._json({"ok": False, "code": self.rpc_failure})
            if self.credential is not None and self.credential != (
                payload["p_key_id"],
                payload["p_secret_digest_hex"],
            ):
                return self._json({"ok": False, "code": "credential_conflict"})
            if self.tenant is None:
                self.tenant = {
                    "id": TENANT_UUID,
                    "tenant_key": payload["p_tenant_key"],
                    "display_name": payload["p_display_name"],
                    "kind": payload["p_kind"],
                    "status": payload["p_tenant_status"],
                    "billing_exempt": False,
                }
            if self.installation is None:
                self.installation = {
                    "id": INSTALLATION_UUID,
                    "tenant_id": TENANT_UUID,
                    "installation_key": payload["p_installation_key"],
                    "label": payload["p_label"],
                    "environment": payload["p_environment"],
                    "channel": payload["p_channel"],
                    "status": "active",
                }
            self.credential = (
                payload["p_key_id"],
                payload["p_secret_digest_hex"],
            )
            return self._json(
                {
                    "ok": True,
                    "tenant_id": TENANT_UUID,
                    "tenant_key": payload["p_tenant_key"],
                    "installation_id": INSTALLATION_UUID,
                    "installation_key": payload["p_installation_key"],
                    "key_id": payload["p_key_id"],
                }
            )
        raise AssertionError(f"unexpected request: {method} {parsed.path} {query}")

    def client(self):
        return provision_prod.SupabaseProvisioningClient(
            SUPABASE_URL,
            SERVICE_ROLE,
            PROJECT_REF,
            transport=self.transport,
        )


def configuration(credentials_file: Path) -> provision_prod.ProvisioningConfig:
    return provision_prod.ProvisioningConfig(
        supabase_url=SUPABASE_URL,
        project_ref=PROJECT_REF,
        service_role_key=SERVICE_ROLE,
        tenant_key=TENANT_KEY,
        company_name="Empresa Real Informada",
        tenant_kind="customer",
        installation_key=INSTALLATION_KEY,
        installation_label="Servidor Windows principal",
        admin_username="admin.prod",
        admin_display_name="Administrador da Plataforma",
        admin_password=ADMIN_PASSWORD,
        credentials_file=credentials_file,
    )


def test_first_provision_and_rerun_are_idempotent_without_sending_plaintext(tmp_path):
    api = FakeSupabase()
    path = tmp_path / "outside-checkout" / "installation.dpapi"
    config = configuration(path)

    first = provision_prod.provision(
        config,
        client=api.client(),
        store_factory=_store_factory,
        secret_factory=lambda: INSTALLATION_SECRET,
    )
    first_credentials = _store_factory(path).load()
    first_disk = path.read_bytes()
    first_rpc = next(
        call
        for call in api.calls
        if call["path"] == "/rest/v1/rpc/np_admin_provision_installation"
    )

    assert first.tenant_key == TENANT_KEY
    assert first_credentials.secret == INSTALLATION_SECRET
    assert INSTALLATION_SECRET.encode("ascii") not in first_disk
    assert TENANT_KEY.encode("ascii") not in first_disk
    assert INSTALLATION_SECRET not in json.dumps(first_rpc["payload"])
    assert first_rpc["payload"]["p_environment"] == "prod"
    assert first_rpc["payload"]["p_channel"] == "prod"
    assert first_rpc["payload"]["p_secret_digest_hex"] == (
        provision_prod.sha256(INSTALLATION_SECRET.encode("utf-8")).hexdigest()
    )
    assert first_rpc["payload"]["p_key_id"] != (
        "key_" + first_rpc["payload"]["p_secret_digest_hex"]
    )
    assert api.admin["password_hash"].startswith("scrypt$")
    assert verify_password(ADMIN_PASSWORD, api.admin["password_hash"])
    assert ADMIN_PASSWORD not in json.dumps(api.admin)

    provision_prod.provision(
        config,
        client=api.client(),
        store_factory=_store_factory,
        secret_factory=lambda: pytest.fail("rerun must reuse the DPAPI secret"),
    )
    second_credentials = _store_factory(path).load()
    admin_posts = [
        call
        for call in api.calls
        if call["method"] == "POST"
        and call["path"] == "/rest/v1/np_platform_users"
    ]
    rpc_posts = [
        call
        for call in api.calls
        if call["path"] == "/rest/v1/rpc/np_admin_provision_installation"
    ]

    assert len(admin_posts) == 1
    assert len(rpc_posts) == 2
    assert rpc_posts[0]["payload"] == rpc_posts[1]["payload"]
    assert second_credentials == first_credentials
    assert path.read_bytes() == first_disk


def test_internal_tenant_receives_explicit_billing_exemption_idempotently(tmp_path):
    api = FakeSupabase()
    config = replace(
        configuration(tmp_path / "internal" / "installation.dpapi"),
        tenant_kind="internal",
        company_name="NexPoint",
    )

    provision_prod.provision(
        config,
        client=api.client(),
        store_factory=_store_factory,
        secret_factory=lambda: INSTALLATION_SECRET,
    )
    provision_prod.provision(
        config,
        client=api.client(),
        store_factory=_store_factory,
        secret_factory=lambda: pytest.fail("rerun must reuse the DPAPI secret"),
    )

    assert api.tenant["kind"] == "internal"
    assert api.tenant["billing_exempt"] is True
    assert len([call for call in api.calls if call["method"] == "PATCH"]) == 2
    assert len([call for call in api.calls if call["method"] == "POST" and call["path"] == "/rest/v1/np_platform_users"]) == 1


def test_existing_remote_installation_without_local_dpapi_fails_before_writes(tmp_path):
    api = FakeSupabase()
    api.tenant = {
        "id": TENANT_UUID,
        "tenant_key": TENANT_KEY,
        "display_name": "Empresa Real Informada",
        "kind": "customer",
        "status": "active",
    }
    api.installation = {
        "id": INSTALLATION_UUID,
        "tenant_id": TENANT_UUID,
        "installation_key": INSTALLATION_KEY,
        "label": "Servidor Windows principal",
        "environment": "prod",
        "channel": "prod",
        "status": "active",
    }
    path = tmp_path / "missing" / "installation.dpapi"

    with pytest.raises(provision_prod.ProvisioningError, match="DPAPI local esta ausente"):
        provision_prod.provision(
            configuration(path),
            client=api.client(),
            store_factory=_store_factory,
            secret_factory=lambda: INSTALLATION_SECRET,
        )

    assert not path.exists()
    assert all(call["method"] == "GET" for call in api.calls)


def test_existing_remote_installation_cannot_silently_add_a_different_credential(tmp_path):
    api = FakeSupabase()
    api.tenant = {
        "id": TENANT_UUID,
        "tenant_key": TENANT_KEY,
        "display_name": "Empresa Real Informada",
        "kind": "customer",
        "status": "active",
    }
    api.installation = {
        "id": INSTALLATION_UUID,
        "tenant_id": TENANT_UUID,
        "installation_key": INSTALLATION_KEY,
        "label": "Servidor Windows principal",
        "environment": "prod",
        "channel": "prod",
        "status": "active",
    }
    path = tmp_path / "mismatched" / "installation.dpapi"
    _store_factory(path).store(
        provision_prod.InstallationCredentials(
            tenant_id=TENANT_KEY,
            installation_id=INSTALLATION_KEY,
            secret=INSTALLATION_SECRET,
        )
    )

    with pytest.raises(provision_prod.ProvisioningError, match="fluxo de rotacao"):
        provision_prod.provision(
            configuration(path),
            client=api.client(),
            store_factory=_store_factory,
            secret_factory=lambda: pytest.fail("existing local secret must be reused"),
        )

    assert any(
        call["path"] == "/rest/v1/np_installation_credentials"
        for call in api.calls
    )
    assert all(
        call["method"] == "GET"
        for call in api.calls
    )


def test_rpc_ok_false_is_rejected_and_retry_keeps_the_same_encrypted_secret(tmp_path):
    api = FakeSupabase()
    api.rpc_failure = "environment_mismatch"
    path = tmp_path / "pending" / "installation.dpapi"
    config = configuration(path)

    with pytest.raises(provision_prod.ProvisioningError, match="environment_mismatch"):
        provision_prod.provision(
            config,
            client=api.client(),
            store_factory=_store_factory,
            secret_factory=lambda: INSTALLATION_SECRET,
        )
    first_disk = path.read_bytes()
    first_secret = _store_factory(path).load().secret

    with pytest.raises(provision_prod.ProvisioningError, match="environment_mismatch"):
        provision_prod.provision(
            config,
            client=api.client(),
            store_factory=_store_factory,
            secret_factory=lambda: pytest.fail("retry generated a different secret"),
        )

    rpc_posts = [
        call
        for call in api.calls
        if call["path"] == "/rest/v1/rpc/np_admin_provision_installation"
    ]
    assert len(rpc_posts) == 2
    assert rpc_posts[0]["payload"] == rpc_posts[1]["payload"]
    assert first_secret == INSTALLATION_SECRET
    assert path.read_bytes() == first_disk
    assert INSTALLATION_SECRET.encode("ascii") not in first_disk


def test_cli_uses_environment_secrets_and_never_prints_them(tmp_path):
    api = FakeSupabase()
    output = StringIO()
    errors = StringIO()
    path = tmp_path / "credentials" / "installation.dpapi"
    environment = {
        "CONTROL_CENTER_SUPABASE_URL": SUPABASE_URL,
        "CONTROL_CENTER_SUPABASE_PROJECT_REF": PROJECT_REF,
        "CONTROL_CENTER_SUPABASE_SERVICE_ROLE_KEY": SERVICE_ROLE,
        "NEXPOINT_PROVISION_ADMIN_PASSWORD": ADMIN_PASSWORD,
        "ERP_ENVIRONMENT": "production",
        "ERP_CHANNEL": "PROD",
        "ERP_TENANT_TYPE": "CUSTOMER",
    }
    argv = [
        "--tenant-key", TENANT_KEY,
        "--company-name", "Empresa Real Informada",
        "--tenant-kind", "customer",
        "--installation-key", INSTALLATION_KEY,
        "--installation-label", "Servidor Windows principal",
        "--admin-username", "admin.prod",
        "--admin-display-name", "Administrador da Plataforma",
        "--credentials-file", str(path),
    ]

    exit_code = provision_prod.main(
        argv,
        environment=environment,
        interactive=False,
        client_factory=lambda _url, _key, _ref: api.client(),
        store_factory=_store_factory,
        secret_factory=lambda: INSTALLATION_SECRET,
        stdout=output,
        stderr=errors,
    )

    rendered = output.getvalue() + errors.getvalue()
    assert exit_code == 0
    assert "Provisionamento PROD concluido" in rendered
    assert SERVICE_ROLE not in rendered
    assert ADMIN_PASSWORD not in rendered
    assert INSTALLATION_SECRET not in rendered
    assert errors.getvalue() == ""


def test_cli_rejects_conflicting_official_url_before_network_and_hides_secrets(tmp_path):
    output = StringIO()
    errors = StringIO()
    called = False

    def client_factory(_url, _key, _ref):
        nonlocal called
        called = True
        raise AssertionError("network client must not be created")

    argv = [
        "--supabase-url", SUPABASE_URL,
        "--project-ref", PROJECT_REF,
        "--tenant-key", TENANT_KEY,
        "--company-name", "Empresa Real Informada",
        "--tenant-kind", "customer",
        "--installation-key", INSTALLATION_KEY,
        "--installation-label", "Servidor Windows principal",
        "--admin-username", "admin.prod",
        "--admin-display-name", "Administrador da Plataforma",
        "--credentials-file", str(tmp_path / "installation.dpapi"),
    ]
    environment = {
        "ERP_SUPABASE_URL": "https://zyxwvutsrqponmlkjihg.supabase.co",
        "CONTROL_CENTER_SUPABASE_SERVICE_ROLE_KEY": SERVICE_ROLE,
        "NEXPOINT_PROVISION_ADMIN_PASSWORD": ADMIN_PASSWORD,
    }

    exit_code = provision_prod.main(
        argv,
        environment=environment,
        interactive=False,
        client_factory=client_factory,
        store_factory=_store_factory,
        secret_factory=lambda: INSTALLATION_SECRET,
        stdout=output,
        stderr=errors,
    )

    rendered = output.getvalue() + errors.getvalue()
    assert exit_code == 1
    assert "diverge da configuracao oficial" in rendered
    assert called is False
    assert SERVICE_ROLE not in rendered
    assert ADMIN_PASSWORD not in rendered
    assert INSTALLATION_SECRET not in rendered


def test_service_role_jwt_must_match_role_and_project_ref():
    def token(claims):
        header = base64_url({"alg": "HS256", "typ": "JWT"})
        payload = base64_url(claims)
        return f"{header}.{payload}.signature-with-enough-length"

    wrong_role = token({"role": "anon", "ref": PROJECT_REF})
    wrong_ref = token({"role": "service_role", "ref": "zyxwvutsrqponmlkjihg"})

    with pytest.raises(provision_prod.ProvisioningError, match="role service_role"):
        provision_prod._service_role_key(wrong_role, project_ref=PROJECT_REF)
    with pytest.raises(provision_prod.ProvisioningError, match="outro projeto"):
        provision_prod._service_role_key(wrong_ref, project_ref=PROJECT_REF)


def test_server_credential_probe_accepts_secret_and_legacy_service_role_jwt():
    jwt = (
        f"{base64_url({'alg': 'HS256', 'typ': 'JWT'})}."
        f"{base64_url({'role': 'service_role', 'ref': PROJECT_REF})}."
        "synthetic-signature-for-transport-test"
    )
    for credential in (SERVICE_ROLE, jwt):
        calls = []

        def transport(method, url, headers, body, timeout):
            calls.append((method, url, headers, body))
            assert method == "GET"
            assert url == f"{SUPABASE_URL}/auth/v1/admin/users?page=1&per_page=1"
            assert headers["apikey"] == credential
            if credential == jwt:
                assert headers["Authorization"] == f"Bearer {jwt}"
            else:
                assert "Authorization" not in headers
            assert body is None
            return 200, {"Content-Type": "application/json"}, b'{"users":[]}'

        client = provision_prod.SupabaseProvisioningClient(
            SUPABASE_URL, credential, PROJECT_REF, transport=transport
        )
        client.validate_server_credential()
        assert len(calls) == 1


@pytest.mark.parametrize(
    "credential",
    [
        None,
        "",
        "short",
        "X" * 40,
        "sb_publishable_" + "P" * 40,
        "sb_secret_invalid space",
        "SUBSTITUA_CHAVE_SERVICE_ROLE",
    ],
)
def test_public_or_malformed_keys_are_rejected_before_network(credential):
    with pytest.raises(provision_prod.ProvisioningError):
        provision_prod._service_role_key(credential, project_ref=PROJECT_REF)


@pytest.mark.parametrize(
    "status,payload",
    [(401, []), (403, []), (404, []), (500, []), (200, [{"id": ADMIN_UUID}])],
)
def test_server_credential_probe_rejects_remote_failure_without_exposing_key(
    status, payload
):
    api = FakeSupabase()
    api.probe_status = status
    api.probe_payload = payload

    with pytest.raises(provision_prod.ProvisioningError) as error:
        api.client().validate_server_credential()

    assert str(error.value) == "Supabase server credential: invalid."
    assert SERVICE_ROLE not in str(error.value)
    assert len(api.calls) == 1


@pytest.mark.parametrize(
    "headers,body",
    [
        ({"Content-Type": "text/html"}, b"[]"),
        ({"Content-Type": "application/json"}, b"not-json"),
    ],
)
def test_server_credential_probe_rejects_malformed_response(headers, body):
    def transport(*_args):
        return 200, headers, body

    client = provision_prod.SupabaseProvisioningClient(
        SUPABASE_URL, SERVICE_ROLE, PROJECT_REF, transport=transport
    )
    with pytest.raises(provision_prod.ProvisioningError) as error:
        client.validate_server_credential()
    assert str(error.value) == "Supabase server credential: invalid."


def test_valid_looking_keys_for_other_project_or_forged_jwt_fail_remote_probe():
    forged_jwt = (
        f"{base64_url({'alg': 'HS256', 'typ': 'JWT'})}."
        f"{base64_url({'role': 'service_role', 'ref': PROJECT_REF})}."
        "forged-signature-with-enough-length"
    )
    for credential in ("sb_secret_" + "W" * 40, forged_jwt):
        calls = []

        def transport(method, url, headers, body, timeout):
            calls.append(method)
            return 401, {"Content-Type": "application/json"}, b"{}"

        client = provision_prod.SupabaseProvisioningClient(
            SUPABASE_URL, credential, PROJECT_REF, transport=transport
        )
        with pytest.raises(provision_prod.ProvisioningError) as error:
            client.validate_server_credential()
        assert str(error.value) == "Supabase server credential: invalid."
        assert credential not in str(error.value)
        assert calls == ["GET"]


def test_invalid_remote_credential_stops_before_any_write_or_dpapi(tmp_path):
    api = FakeSupabase()
    api.probe_status = 401
    path = tmp_path / "credentials" / "installation.dpapi"

    with pytest.raises(provision_prod.ProvisioningError, match="credential: invalid"):
        provision_prod.provision(
            configuration(path),
            client=api.client(),
            store_factory=_store_factory,
            secret_factory=lambda: pytest.fail("secret must not be created"),
        )

    assert [(call["method"], call["path"]) for call in api.calls] == [
        ("GET", "/auth/v1/admin/users")
    ]
    assert not path.exists()


def test_missing_cloud_schema_is_not_reported_as_invalid_credential(tmp_path):
    api = FakeSupabase()
    api.admin_select_status = 404
    path = tmp_path / "installation.dpapi"

    with pytest.raises(provision_prod.ProvisioningError, match="schema Cloud PROD"):
        provision_prod.provision(
            configuration(path), client=api.client(), store_factory=_store_factory
        )

    assert [(call["method"], call["path"]) for call in api.calls] == [
        ("GET", "/auth/v1/admin/users"),
        ("GET", "/rest/v1/np_platform_users"),
    ]
    assert not path.exists()


def test_cli_does_not_print_credential_on_remote_rejection(tmp_path):
    api = FakeSupabase()
    api.probe_status = 401
    output = StringIO()
    errors = StringIO()
    path = tmp_path / "installation.dpapi"
    environment = {
        "CONTROL_CENTER_SUPABASE_URL": SUPABASE_URL,
        "CONTROL_CENTER_SUPABASE_PROJECT_REF": PROJECT_REF,
        "CONTROL_CENTER_SUPABASE_SERVICE_ROLE_KEY": SERVICE_ROLE,
        "NEXPOINT_PROVISION_ADMIN_PASSWORD": ADMIN_PASSWORD,
    }
    argv = [
        "--tenant-key", TENANT_KEY,
        "--company-name", "NexPoint",
        "--tenant-kind", "internal",
        "--installation-key", INSTALLATION_KEY,
        "--installation-label", "PC Principal NexPoint",
        "--admin-username", "nexpoint-admin",
        "--admin-display-name", "Administrador NexPoint",
        "--credentials-file", str(path),
    ]

    exit_code = provision_prod.main(
        argv,
        environment=environment,
        interactive=False,
        client_factory=lambda _url, _key, _ref: api.client(),
        store_factory=_store_factory,
        stdout=output,
        stderr=errors,
    )

    rendered = output.getvalue() + errors.getvalue()
    assert exit_code == 1
    assert "Supabase server credential: invalid." in rendered
    assert SERVICE_ROLE not in rendered
    assert ADMIN_PASSWORD not in rendered
    assert not path.exists()


def test_wrong_project_ref_is_rejected_before_network(tmp_path):
    api = FakeSupabase()
    wrong_ref = "zyxwvutsrqponmlkjihg"
    config = replace(
        configuration(tmp_path / "installation.dpapi"),
        supabase_url=f"https://{wrong_ref}.supabase.co",
        project_ref=wrong_ref,
    )

    with pytest.raises(provision_prod.ProvisioningError, match="project ref"):
        provision_prod.provision(config, client=api.client(), store_factory=_store_factory)

    assert api.calls == []


def base64_url(value):
    import base64

    return base64.urlsafe_b64encode(
        json.dumps(value, separators=(",", ":")).encode("utf-8")
    ).rstrip(b"=").decode("ascii")
