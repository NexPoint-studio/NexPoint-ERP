"""Bootstrap seguro e idempotente do Control Center e de uma instalacao PROD.

O processo usa somente a Data API oficial do projeto Supabase informado. A
service role e a senha inicial nunca sao aceitas como argumentos de linha de
comando: devem vir do ambiente ou de ``getpass``. O segredo da instalacao e
gerado localmente, persistido com DPAPI e enviado ao Supabase apenas como
SHA-256.
"""

from __future__ import annotations

import argparse
import base64
from dataclasses import dataclass, field
import getpass
from hashlib import sha256
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import socket
import sys
from typing import Any, Callable, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import UUID


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.installation_identity import (  # noqa: E402
    InstallationCredentialError,
    InstallationCredentials,
    InstallationCredentialStore,
)
from control_center.security import hash_password, verify_password  # noqa: E402


_PROJECT_REF = re.compile(r"^[a-z0-9]{20}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][-A-Za-z0-9._:@/+]*$")
_USERNAME = re.compile(r"^[a-z0-9][-a-z0-9@._+]{2,179}$")
_SB_SECRET = re.compile(r"^sb_secret_[A-Za-z0-9_-]{20,}$")
_MAX_RESPONSE_BYTES = 1_000_000
_TIMEOUT_SECONDS = 15.0
_KNOWN_RPC_FAILURES = frozenset(
    {
        "credential_conflict",
        "environment_mismatch",
        "installation_conflict",
        "invalid_payload",
        "tenant_conflict",
    }
)


class ProvisioningError(RuntimeError):
    """A bounded error whose message never contains credential material."""


@dataclass(frozen=True, slots=True)
class ProvisioningConfig:
    supabase_url: str
    project_ref: str
    service_role_key: str = field(repr=False)
    tenant_key: str
    company_name: str
    tenant_kind: str
    installation_key: str
    installation_label: str
    admin_username: str
    admin_display_name: str
    admin_password: str = field(repr=False)
    credentials_file: Path


@dataclass(frozen=True, slots=True)
class ProvisioningResult:
    tenant_key: str
    installation_key: str
    admin_username: str
    credentials_file: Path


Transport = Callable[
    [str, str, Mapping[str, str], bytes | None, float],
    tuple[int, Mapping[str, str], bytes],
]


def _plain_text(value: object, *, field_name: str, maximum: int) -> str:
    candidate = str(value or "").strip()
    if (
        not 1 <= len(candidate) <= maximum
        or any(ord(character) < 32 or ord(character) == 127 for character in candidate)
        or candidate.casefold().startswith("substitua_")
    ):
        raise ProvisioningError(f"{field_name} invalido.")
    return candidate


def _identifier(value: object, *, field_name: str, maximum: int) -> str:
    candidate = str(value or "").strip()
    # The DPAPI contract requires opaque IDs with at least eight characters.
    if not 8 <= len(candidate) <= maximum or not _IDENTIFIER.fullmatch(candidate):
        raise ProvisioningError(f"{field_name} invalido.")
    return candidate


def _username(value: object) -> str:
    candidate = str(value or "").strip().casefold()
    if not _USERNAME.fullmatch(candidate) or candidate.startswith("substitua"):
        raise ProvisioningError("admin_username invalido.")
    return candidate


def _password(value: object) -> str:
    candidate = str(value or "")
    if (
        not 12 <= len(candidate) <= 256
        or "\x00" in candidate
        or candidate.startswith("SUBSTITUA_")
    ):
        raise ProvisioningError("A senha do platform_admin deve ter entre 12 e 256 caracteres.")
    return candidate


def _official_supabase_origin(raw_url: object, project_ref: object) -> tuple[str, str]:
    reference = str(project_ref or "").strip().casefold()
    if not _PROJECT_REF.fullmatch(reference):
        raise ProvisioningError("O project ref Supabase oficial e invalido.")

    value = str(raw_url or "").strip().rstrip("/")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise ProvisioningError("A URL Supabase oficial e invalida.") from None
    expected_host = f"{reference}.supabase.co"
    if (
        parsed.scheme != "https"
        or parsed.hostname != expected_host
        or parsed.netloc.casefold() != expected_host
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or port not in {None, 443}
    ):
        raise ProvisioningError(
            "A URL deve corresponder exatamente ao project ref oficial em supabase.co."
        )
    return f"https://{expected_host}", reference


def _decode_jwt_claims(value: str) -> Mapping[str, Any]:
    parts = value.split(".")
    if len(parts) != 3:
        raise ProvisioningError("A chave Supabase informada nao e uma service role valida.")
    try:
        encoded = parts[1] + "=" * (-len(parts[1]) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(encoded).decode("utf-8"))
    except (UnicodeError, ValueError, json.JSONDecodeError):
        raise ProvisioningError("A chave Supabase informada e invalida.") from None
    if not isinstance(decoded, Mapping):
        raise ProvisioningError("A chave Supabase informada e invalida.")
    return decoded


def _service_role_key(raw: object, *, project_ref: str) -> str:
    value = str(raw or "").strip()
    if len(value) < 32 or value.startswith(("SUBSTITUA_", "sb_publishable_")):
        raise ProvisioningError("Informe uma service role Supabase server-only valida.")
    if value.startswith("sb_secret_"):
        if not _SB_SECRET.fullmatch(value):
            raise ProvisioningError("A chave Supabase server-only possui formato invalido.")
        return value

    claims = _decode_jwt_claims(value)
    if claims.get("role") != "service_role":
        raise ProvisioningError("A chave Supabase nao possui role service_role.")
    claimed_ref = claims.get("ref")
    if claimed_ref is not None and claimed_ref != project_ref:
        raise ProvisioningError("A service role pertence a outro projeto Supabase.")
    return value


def _resolved_outside_checkout(raw: object) -> Path:
    candidate = Path(str(raw or "")).expanduser()
    if not candidate.is_absolute():
        raise ProvisioningError("O arquivo DPAPI deve usar caminho absoluto.")
    if candidate.is_symlink():
        raise ProvisioningError("O arquivo DPAPI nao pode ser um link.")
    resolved = candidate.resolve()
    try:
        resolved.relative_to(ROOT.resolve())
    except ValueError:
        pass
    else:
        raise ProvisioningError("O arquivo DPAPI de PROD deve ficar fora do checkout.")
    if resolved.exists() and not resolved.is_file():
        raise ProvisioningError("O destino DPAPI e invalido.")
    return resolved


def _validate_config(config: ProvisioningConfig) -> ProvisioningConfig:
    url, reference = _official_supabase_origin(config.supabase_url, config.project_ref)
    tenant_kind = str(config.tenant_kind or "").strip().casefold()
    if tenant_kind not in {"customer", "internal"}:
        raise ProvisioningError("tenant_kind de PROD deve ser customer ou internal.")
    return ProvisioningConfig(
        supabase_url=url,
        project_ref=reference,
        service_role_key=_service_role_key(
            config.service_role_key, project_ref=reference
        ),
        tenant_key=_identifier(
            config.tenant_key, field_name="tenant_key", maximum=80
        ),
        company_name=_plain_text(
            config.company_name, field_name="company_name", maximum=160
        ),
        tenant_kind=tenant_kind,
        installation_key=_identifier(
            config.installation_key, field_name="installation_key", maximum=120
        ),
        installation_label=_plain_text(
            config.installation_label,
            field_name="installation_label",
            maximum=160,
        ),
        admin_username=_username(config.admin_username),
        admin_display_name=_plain_text(
            config.admin_display_name,
            field_name="admin_display_name",
            maximum=160,
        ),
        admin_password=_password(config.admin_password),
        credentials_file=_resolved_outside_checkout(config.credentials_file),
    )


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


def _default_transport(
    method: str,
    url: str,
    headers: Mapping[str, str],
    body: bytes | None,
    timeout: float,
) -> tuple[int, Mapping[str, str], bytes]:
    request = Request(url, data=body, headers=dict(headers), method=method)
    opener = build_opener(_NoRedirect())
    try:
        with opener.open(request, timeout=timeout) as response:
            raw = response.read(_MAX_RESPONSE_BYTES + 1)
            if len(raw) > _MAX_RESPONSE_BYTES:
                raise ProvisioningError("A resposta Supabase excedeu o limite seguro.")
            if response.geturl() != url:
                raise ProvisioningError("O Supabase tentou redirecionar a requisicao.")
            return int(response.status), dict(response.headers.items()), raw
    except HTTPError as exc:
        # Never reflect a PostgREST body; it can contain submitted row values.
        exc.read(64_000)
        return int(exc.code), dict(exc.headers.items()), b""
    except (URLError, TimeoutError, socket.timeout, OSError) as exc:
        raise ProvisioningError("Nao foi possivel acessar o projeto Supabase oficial.") from exc


class SupabaseProvisioningClient:
    """Minimal server-only Data API client used only by this bootstrap."""

    def __init__(
        self,
        url: str,
        service_role_key: str,
        project_ref: str,
        *,
        transport: Transport | None = None,
    ) -> None:
        official_url, official_ref = _official_supabase_origin(url, project_ref)
        self._base_url = official_url
        self._project_ref = official_ref
        self._service_role_key = _service_role_key(
            service_role_key, project_ref=official_ref
        )
        self._transport = transport or _default_transport

    def _request(
        self,
        method: str,
        path: str,
        *,
        query: Sequence[tuple[str, object]] = (),
        payload: object | None = None,
        prefer: str | None = None,
        expected: tuple[int, ...] = (200,),
    ) -> Any:
        if not path.startswith("/rest/v1/") or ".." in path:
            raise ProvisioningError("Caminho Supabase recusado.")
        suffix = "?" + urlencode(
            [(key, str(value)) for key, value in query]
        ) if query else ""
        raw_body = None
        headers = {
            "Accept": "application/json",
            "apikey": self._service_role_key,
            "User-Agent": "NexPoint-ERP-Provisioner/1",
        }
        if not self._service_role_key.startswith("sb_secret_"):
            headers["Authorization"] = f"Bearer {self._service_role_key}"
        if payload is not None:
            raw_body = json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
                allow_nan=False,
            ).encode("utf-8")
            if len(raw_body) > 64 * 1024:
                raise ProvisioningError("O payload de provisionamento excedeu o limite.")
            headers["Content-Type"] = "application/json"
        if prefer:
            headers["Prefer"] = prefer

        status, response_headers, raw = self._transport(
            method,
            f"{self._base_url}{path}{suffix}",
            headers,
            raw_body,
            _TIMEOUT_SECONDS,
        )
        if status not in expected:
            raise ProvisioningError(
                "O Supabase recusou uma operacao de provisionamento."
            )
        content_type = next(
            (
                str(value).split(";", 1)[0].strip().casefold()
                for key, value in response_headers.items()
                if str(key).casefold() == "content-type"
            ),
            "",
        )
        if content_type != "application/json" or not raw:
            raise ProvisioningError("O Supabase retornou uma resposta sem contrato JSON.")
        if len(raw) > _MAX_RESPONSE_BYTES:
            raise ProvisioningError("A resposta Supabase excedeu o limite seguro.")
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            raise ProvisioningError("O Supabase retornou JSON invalido.") from None

    def _select_one(
        self,
        table: str,
        *,
        select: str,
        filter_name: str,
        filter_value: str,
    ) -> Mapping[str, Any] | None:
        result = self._request(
            "GET",
            f"/rest/v1/{table}",
            query=(
                ("select", select),
                (filter_name, f"eq.{filter_value}"),
                ("limit", 2),
            ),
        )
        if not isinstance(result, list) or any(
            not isinstance(row, Mapping) for row in result
        ):
            raise ProvisioningError("O Supabase retornou uma consulta invalida.")
        if len(result) > 1:
            raise ProvisioningError("O Supabase retornou identidade duplicada.")
        return dict(result[0]) if result else None

    def get_platform_admin(self, username: str) -> Mapping[str, Any] | None:
        return self._select_one(
            "np_platform_users",
            select="id,username,display_name,role,password_hash,active",
            filter_name="username",
            filter_value=username,
        )

    def create_platform_admin(
        self,
        *,
        username: str,
        display_name: str,
        password_hash: str,
    ) -> Mapping[str, Any]:
        result = self._request(
            "POST",
            "/rest/v1/np_platform_users",
            payload={
                "username": username,
                "display_name": display_name,
                "role": "platform_admin",
                "password_hash": password_hash,
                "active": True,
            },
            prefer="return=representation",
            expected=(200, 201),
        )
        if (
            not isinstance(result, list)
            or len(result) != 1
            or not isinstance(result[0], Mapping)
        ):
            raise ProvisioningError("O Supabase nao confirmou o platform_admin.")
        return dict(result[0])

    def get_tenant(self, tenant_key: str) -> Mapping[str, Any] | None:
        return self._select_one(
            "np_tenants",
            select="id,tenant_key,display_name,kind,status",
            filter_name="tenant_key",
            filter_value=tenant_key,
        )

    def get_installation(self, installation_key: str) -> Mapping[str, Any] | None:
        return self._select_one(
            "np_installations",
            select="id,tenant_id,installation_key,label,environment,channel,status",
            filter_name="installation_key",
            filter_value=installation_key,
        )

    def get_installation_credential(
        self, *, installation_id: str, key_id: str
    ) -> Mapping[str, Any] | None:
        result = self._request(
            "GET",
            "/rest/v1/np_installation_credentials",
            query=(
                (
                    "select",
                    "installation_id,key_id,kind,secret_digest,revoked_at,expires_at",
                ),
                ("installation_id", f"eq.{installation_id}"),
                ("key_id", f"eq.{key_id}"),
                ("limit", 2),
            ),
        )
        if not isinstance(result, list) or any(
            not isinstance(row, Mapping) for row in result
        ):
            raise ProvisioningError("O Supabase retornou uma credencial invalida.")
        if len(result) > 1:
            raise ProvisioningError("O Supabase retornou credencial duplicada.")
        return dict(result[0]) if result else None

    def provision_installation(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        result = self._request(
            "POST",
            "/rest/v1/rpc/np_admin_provision_installation",
            payload=payload,
        )
        if isinstance(result, list):
            if len(result) != 1 or not isinstance(result[0], Mapping):
                raise ProvisioningError("O RPC retornou um contrato ambiguo.")
            result = result[0]
        if not isinstance(result, Mapping):
            raise ProvisioningError("O RPC retornou um contrato invalido.")
        if result.get("ok") is not True:
            code = str(result.get("code") or "")
            suffix = f" ({code})" if code in _KNOWN_RPC_FAILURES else ""
            raise ProvisioningError(f"O RPC recusou o provisionamento{suffix}.")
        return dict(result)


def _valid_uuid(value: object) -> bool:
    try:
        parsed = UUID(str(value))
    except (AttributeError, TypeError, ValueError):
        return False
    return str(parsed) == str(value).casefold()


def _validate_admin_row(
    row: Mapping[str, Any], config: ProvisioningConfig
) -> None:
    encoded = row.get("password_hash")
    if (
        not _valid_uuid(row.get("id"))
        or row.get("username") != config.admin_username
        or row.get("display_name") != config.admin_display_name
        or row.get("role") != "platform_admin"
        or row.get("active") is not True
        or not isinstance(encoded, str)
        or not encoded.startswith("scrypt$")
        or not verify_password(config.admin_password, encoded)
    ):
        raise ProvisioningError(
            "O platform_admin existente nao corresponde ao bootstrap informado."
        )


def _validate_remote_state(
    *,
    config: ProvisioningConfig,
    tenant: Mapping[str, Any] | None,
    installation: Mapping[str, Any] | None,
) -> None:
    if tenant is not None and (
        not _valid_uuid(tenant.get("id"))
        or tenant.get("tenant_key") != config.tenant_key
        or tenant.get("display_name") != config.company_name
        or tenant.get("kind") != config.tenant_kind
        or tenant.get("status") != "active"
    ):
        raise ProvisioningError(
            "O tenant existente nao corresponde aos dados informados."
        )
    if installation is None:
        return
    if tenant is None or (
        not _valid_uuid(installation.get("id"))
        or installation.get("tenant_id") != tenant.get("id")
        or installation.get("installation_key") != config.installation_key
        or installation.get("label") != config.installation_label
        or installation.get("environment") != "prod"
        or installation.get("channel") != "prod"
        or installation.get("status") != "active"
    ):
        raise ProvisioningError(
            "A instalacao existente nao corresponde ao bootstrap informado."
        )


def _load_or_create_credentials(
    *,
    config: ProvisioningConfig,
    remote_installation_exists: bool,
    store_factory: Callable[[Path], InstallationCredentialStore],
    secret_factory: Callable[[], str],
) -> InstallationCredentials:
    path = config.credentials_file
    if path.is_symlink():
        raise ProvisioningError("O arquivo DPAPI nao pode ser um link.")
    store = store_factory(path)
    if path.exists():
        credentials = store.load()
    else:
        if remote_installation_exists:
            raise ProvisioningError(
                "A instalacao remota existe, mas a credencial DPAPI local esta ausente."
            )
        credentials = InstallationCredentials(
            tenant_id=config.tenant_key,
            installation_id=config.installation_key,
            secret=secret_factory(),
            tenant_kind=config.tenant_kind,
        )
        store.store(credentials)
        credentials = store.load()

    if (
        credentials.tenant_id != config.tenant_key
        or credentials.installation_id != config.installation_key
        or credentials.tenant_kind.casefold() != config.tenant_kind
    ):
        raise ProvisioningError(
            "A credencial DPAPI pertence a outro tenant ou instalacao."
        )
    return credentials


def _ensure_platform_admin(
    client: SupabaseProvisioningClient, config: ProvisioningConfig,
    existing: Mapping[str, Any] | None,
) -> None:
    if existing is not None:
        _validate_admin_row(existing, config)
        return
    created = client.create_platform_admin(
        username=config.admin_username,
        display_name=config.admin_display_name,
        password_hash=hash_password(config.admin_password),
    )
    _validate_admin_row(created, config)


def _installation_key_id(
    credentials: InstallationCredentials,
) -> str:
    material = (
        "nexpoint-installation-key-id-v1\x00"
        f"{credentials.tenant_id}\x00{credentials.installation_id}\x00"
        f"{credentials.secret}"
    ).encode("utf-8")
    return f"key_{sha256(material).hexdigest()}"


def _validate_existing_remote_credential(
    row: Mapping[str, Any] | None,
    *,
    installation_id: str,
    key_id: str,
    secret_digest: str,
) -> None:
    if row is None:
        raise ProvisioningError(
            "A instalacao remota nao possui a credencial DPAPI local; use o fluxo de rotacao."
        )
    rendered_digest = row.get("secret_digest")
    if (
        row.get("installation_id") != installation_id
        or row.get("key_id") != key_id
        or row.get("kind") != "hmac_sha256"
        or rendered_digest not in {secret_digest, f"\\x{secret_digest}"}
        or row.get("revoked_at") is not None
        or row.get("expires_at") is not None
    ):
        raise ProvisioningError(
            "A credencial remota nao corresponde a credencial DPAPI local."
        )


def provision(
    config: ProvisioningConfig,
    *,
    client: SupabaseProvisioningClient | None = None,
    store_factory: Callable[[Path], InstallationCredentialStore] = InstallationCredentialStore,
    secret_factory: Callable[[], str] = lambda: secrets.token_urlsafe(48),
) -> ProvisioningResult:
    """Provision the exact requested state or fail without replacing identities."""

    config = _validate_config(config)
    remote = client or SupabaseProvisioningClient(
        config.supabase_url, config.service_role_key, config.project_ref
    )

    existing_admin = remote.get_platform_admin(config.admin_username)
    tenant = remote.get_tenant(config.tenant_key)
    installation = remote.get_installation(config.installation_key)
    if existing_admin is not None:
        _validate_admin_row(existing_admin, config)
    _validate_remote_state(
        config=config, tenant=tenant, installation=installation
    )

    credentials = _load_or_create_credentials(
        config=config,
        remote_installation_exists=installation is not None,
        store_factory=store_factory,
        secret_factory=secret_factory,
    )
    secret_digest = sha256(credentials.secret.encode("utf-8")).hexdigest()
    key_id = _installation_key_id(credentials)
    if installation is not None:
        remote_credential = remote.get_installation_credential(
            installation_id=str(installation["id"]), key_id=key_id
        )
        _validate_existing_remote_credential(
            remote_credential,
            installation_id=str(installation["id"]),
            key_id=key_id,
            secret_digest=secret_digest,
        )
    response = remote.provision_installation(
        {
            "p_tenant_key": config.tenant_key,
            "p_display_name": config.company_name,
            "p_kind": config.tenant_kind,
            "p_tenant_status": "active",
            "p_installation_key": config.installation_key,
            "p_label": config.installation_label,
            "p_environment": "prod",
            "p_channel": "prod",
            "p_key_id": key_id,
            "p_secret_digest_hex": secret_digest,
        }
    )
    if (
        set(response)
        != {
            "ok",
            "tenant_id",
            "tenant_key",
            "installation_id",
            "installation_key",
            "key_id",
        }
        or response.get("tenant_key") != config.tenant_key
        or response.get("installation_key") != config.installation_key
        or response.get("key_id") != key_id
        or not _valid_uuid(response.get("tenant_id"))
        or not _valid_uuid(response.get("installation_id"))
    ):
        raise ProvisioningError("O RPC nao confirmou a identidade provisionada.")

    # Create the operator only after the tenant/installation transaction has
    # succeeded. A later admin failure is safely retryable without leaving an
    # unrelated privileged account behind when provisioning is rejected.
    _ensure_platform_admin(remote, config, existing_admin)

    # A final DPAPI read proves that the exact secret needed by the ERP remains
    # recoverable by this Windows account after the remote write.
    persisted = store_factory(config.credentials_file).load()
    if (
        persisted.tenant_id != config.tenant_key
        or persisted.installation_id != config.installation_key
        or persisted.tenant_kind.casefold() != config.tenant_kind
        or not hmac.compare_digest(persisted.secret, credentials.secret)
    ):
        raise ProvisioningError("A verificacao final da credencial DPAPI falhou.")

    return ProvisioningResult(
        tenant_key=config.tenant_key,
        installation_key=config.installation_key,
        admin_username=config.admin_username,
        credentials_file=config.credentials_file,
    )


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ProvisioningError("Os argumentos do provisionamento sao invalidos.")


def _build_parser() -> argparse.ArgumentParser:
    parser = _SafeArgumentParser(
        description="Provisiona platform_admin e instalacao ERP no Supabase PROD."
    )
    parser.add_argument("--supabase-url")
    parser.add_argument("--project-ref")
    parser.add_argument("--tenant-key")
    parser.add_argument("--company-name")
    parser.add_argument("--tenant-kind", choices=("customer", "internal"))
    parser.add_argument("--installation-key")
    parser.add_argument("--installation-label")
    parser.add_argument("--admin-username")
    parser.add_argument("--admin-display-name")
    parser.add_argument("--credentials-file")
    return parser


def _same_config_value(
    cli_value: object,
    environment: Mapping[str, str],
    env_names: Sequence[str],
    *,
    field_name: str,
    normalize: Callable[[str], str] = lambda value: value.strip(),
) -> str:
    candidates: list[str] = []
    if cli_value is not None and str(cli_value).strip():
        candidates.append(normalize(str(cli_value)))
    for name in env_names:
        raw = environment.get(name, "")
        if raw.strip():
            candidates.append(normalize(raw))
    if len(set(candidates)) > 1:
        raise ProvisioningError(
            f"{field_name} diverge da configuracao oficial do ambiente."
        )
    return candidates[0] if candidates else ""


def _required_value(
    cli_value: object,
    environment: Mapping[str, str],
    env_name: str,
    *,
    field_name: str,
    prompt: str,
    interactive: bool,
    input_fn: Callable[[str], str],
    normalize: Callable[[str], str] = lambda value: value.strip(),
) -> str:
    value = _same_config_value(
        cli_value,
        environment,
        (env_name,),
        field_name=field_name,
        normalize=normalize,
    )
    if not value:
        if not interactive:
            raise ProvisioningError(
                f"Informe --{field_name.replace('_', '-')} ou {env_name}."
            )
        value = normalize(input_fn(prompt))
    if not value:
        raise ProvisioningError(f"{field_name} e obrigatorio.")
    return value


def _secret_from_environment_or_prompt(
    environment: Mapping[str, str],
    env_names: Sequence[str],
    *,
    label: str,
    prompt: str,
    interactive: bool,
    getpass_fn: Callable[[str], str],
    confirm: bool = False,
) -> str:
    supplied = [environment[name] for name in env_names if environment.get(name, "")]
    if len(set(supplied)) > 1:
        raise ProvisioningError(f"{label} diverge entre variaveis de ambiente.")
    if supplied:
        return supplied[0]
    if not interactive:
        raise ProvisioningError(
            f"{label} deve vir de uma variavel de ambiente ou getpass."
        )
    value = getpass_fn(prompt)
    if confirm:
        repeated = getpass_fn("Confirme a senha do platform_admin: ")
        if not hmac.compare_digest(value, repeated):
            raise ProvisioningError("A confirmacao da senha do platform_admin diverge.")
    return value


def _credentials_path(
    cli_value: object, environment: Mapping[str, str]
) -> Path:
    configured = environment.get("ERP_INSTALLATION_CREDENTIALS_FILE", "").strip()
    if cli_value is not None and str(cli_value).strip():
        cli_path = Path(str(cli_value)).expanduser()
        if configured and cli_path.resolve() != Path(configured).expanduser().resolve():
            raise ProvisioningError(
                "credentials_file diverge de ERP_INSTALLATION_CREDENTIALS_FILE."
            )
        return cli_path
    if configured:
        return Path(configured).expanduser()

    configured_data = environment.get("ERP_DATA_DIR", "").strip()
    if configured_data:
        data_directory = Path(configured_data).expanduser()
        if not data_directory.is_absolute():
            raise ProvisioningError("ERP_DATA_DIR deve ser absoluto em PROD.")
        return data_directory.resolve().parent / "credentials" / "installation.dpapi"
    local_app_data = environment.get("LOCALAPPDATA", "").strip()
    if not local_app_data:
        raise ProvisioningError(
            "Informe --credentials-file ou configure LOCALAPPDATA/ERP_DATA_DIR."
        )
    return (
        Path(local_app_data).expanduser()
        / "NexPoint"
        / "ERP"
        / "credentials"
        / "installation.dpapi"
    )


def _reject_non_production_config(
    environment: Mapping[str, str], *, tenant_kind: str
) -> None:
    exact = {
        "ERP_ENVIRONMENT": "production",
        "ERP_CHANNEL": "prod",
        "ERP_TENANT_TYPE": tenant_kind,
        "CONTROL_CENTER_ENVIRONMENT": "production",
        "CONTROL_CENTER_STORAGE": "supabase",
    }
    aliases = {"ERP_ENVIRONMENT": {"prod", "production"}}
    for name, expected in exact.items():
        raw = environment.get(name, "").strip().casefold()
        if not raw:
            continue
        accepted = aliases.get(name, {expected})
        if raw not in accepted:
            raise ProvisioningError(f"{name} diverge do provisionamento PROD.")
    for name in ("ERP_QA_MODE", "CONTROL_CENTER_QA_MODE", "CONTROL_CENTER_SEED_DEMO"):
        raw = environment.get(name, "").strip().casefold()
        if raw in {"1", "true", "yes", "on"}:
            raise ProvisioningError(f"{name} e proibido neste provisionamento PROD.")


def _collect_config(
    namespace: argparse.Namespace,
    *,
    environment: Mapping[str, str],
    interactive: bool,
    input_fn: Callable[[str], str],
    getpass_fn: Callable[[str], str],
) -> ProvisioningConfig:
    supabase_url = _same_config_value(
        namespace.supabase_url,
        environment,
        (
            "CONTROL_CENTER_SUPABASE_URL",
            "ERP_SUPABASE_URL",
            "SUPABASE_URL",
        ),
        field_name="supabase_url",
        normalize=lambda value: value.strip().rstrip("/"),
    )
    project_ref = _same_config_value(
        namespace.project_ref,
        environment,
        ("CONTROL_CENTER_SUPABASE_PROJECT_REF", "SUPABASE_PROJECT_REF"),
        field_name="project_ref",
        normalize=lambda value: value.strip().casefold(),
    )
    if not supabase_url or not project_ref:
        raise ProvisioningError(
            "Informe a URL e o project ref oficiais do Supabase por flags ou ambiente."
        )

    tenant_kind = _required_value(
        namespace.tenant_kind,
        environment,
        "NEXPOINT_PROVISION_TENANT_KIND",
        field_name="tenant_kind",
        prompt="Tipo do tenant PROD (customer/internal): ",
        interactive=interactive,
        input_fn=input_fn,
        normalize=lambda value: value.strip().casefold(),
    )
    _reject_non_production_config(environment, tenant_kind=tenant_kind)

    service_role = _secret_from_environment_or_prompt(
        environment,
        ("CONTROL_CENTER_SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_SERVICE_ROLE_KEY"),
        label="A service role Supabase",
        prompt="Service role Supabase (entrada oculta): ",
        interactive=interactive,
        getpass_fn=getpass_fn,
    )
    admin_password = _secret_from_environment_or_prompt(
        environment,
        ("NEXPOINT_PROVISION_ADMIN_PASSWORD",),
        label="A senha do platform_admin",
        prompt="Senha inicial do platform_admin (entrada oculta): ",
        interactive=interactive,
        getpass_fn=getpass_fn,
        confirm=True,
    )

    return ProvisioningConfig(
        supabase_url=supabase_url,
        project_ref=project_ref,
        service_role_key=service_role,
        tenant_key=_required_value(
            namespace.tenant_key,
            environment,
            "NEXPOINT_PROVISION_TENANT_KEY",
            field_name="tenant_key",
            prompt="Chave do tenant/empresa: ",
            interactive=interactive,
            input_fn=input_fn,
        ),
        company_name=_required_value(
            namespace.company_name,
            environment,
            "NEXPOINT_PROVISION_COMPANY_NAME",
            field_name="company_name",
            prompt="Nome oficial da empresa: ",
            interactive=interactive,
            input_fn=input_fn,
        ),
        tenant_kind=tenant_kind,
        installation_key=_required_value(
            namespace.installation_key,
            environment,
            "NEXPOINT_PROVISION_INSTALLATION_KEY",
            field_name="installation_key",
            prompt="Chave desta instalacao: ",
            interactive=interactive,
            input_fn=input_fn,
        ),
        installation_label=_required_value(
            namespace.installation_label,
            environment,
            "NEXPOINT_PROVISION_INSTALLATION_LABEL",
            field_name="installation_label",
            prompt="Rotulo desta instalacao: ",
            interactive=interactive,
            input_fn=input_fn,
        ),
        admin_username=_required_value(
            namespace.admin_username,
            environment,
            "NEXPOINT_PROVISION_ADMIN_USERNAME",
            field_name="admin_username",
            prompt="Login do platform_admin: ",
            interactive=interactive,
            input_fn=input_fn,
            normalize=lambda value: value.strip().casefold(),
        ),
        admin_display_name=_required_value(
            namespace.admin_display_name,
            environment,
            "NEXPOINT_PROVISION_ADMIN_DISPLAY_NAME",
            field_name="admin_display_name",
            prompt="Nome de exibicao do platform_admin: ",
            interactive=interactive,
            input_fn=input_fn,
        ),
        admin_password=admin_password,
        credentials_file=_credentials_path(namespace.credentials_file, environment),
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    environment: Mapping[str, str] | None = None,
    interactive: bool | None = None,
    input_fn: Callable[[str], str] | None = None,
    getpass_fn: Callable[[str], str] | None = None,
    client_factory: Callable[
        [str, str, str], SupabaseProvisioningClient
    ] = SupabaseProvisioningClient,
    store_factory: Callable[[Path], InstallationCredentialStore] = InstallationCredentialStore,
    secret_factory: Callable[[], str] = lambda: secrets.token_urlsafe(48),
    stdout=None,
    stderr=None,
) -> int:
    output = stdout or sys.stdout
    error_output = stderr or sys.stderr
    env = dict(os.environ if environment is None else environment)
    prompt_input = input_fn or input
    secret_input = getpass_fn or getpass.getpass
    is_interactive = sys.stdin.isatty() if interactive is None else interactive
    try:
        namespace = _build_parser().parse_args(argv)
        config = _collect_config(
            namespace,
            environment=env,
            interactive=is_interactive,
            input_fn=prompt_input,
            getpass_fn=secret_input,
        )
        validated = _validate_config(config)
        client = client_factory(
            validated.supabase_url,
            validated.service_role_key,
            validated.project_ref,
        )
        result = provision(
            validated,
            client=client,
            store_factory=store_factory,
            secret_factory=secret_factory,
        )
    except (ProvisioningError, InstallationCredentialError) as exc:
        print(f"ERRO: {exc}", file=error_output)
        return 1
    except KeyboardInterrupt:
        print("ERRO: Provisionamento interrompido.", file=error_output)
        return 130
    except Exception:
        # Fail closed without reflecting exception arguments that may contain a
        # request, environment value or credential supplied by the operator.
        print("ERRO: Falha inesperada no provisionamento seguro.", file=error_output)
        return 1

    print("Provisionamento PROD concluido e validado.", file=output)
    print(f"tenant_key: {result.tenant_key}", file=output)
    print(f"installation_key: {result.installation_key}", file=output)
    print(f"platform_admin: {result.admin_username}", file=output)
    print(f"credencial DPAPI: {result.credentials_file}", file=output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
