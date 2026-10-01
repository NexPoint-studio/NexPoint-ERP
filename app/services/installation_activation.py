"""First-device enrollment. Passwords are transient; local state is DPAPI-only.

The package carries public scope, never a credential. Enrollment sends a digest
of a locally generated key over verified HTTPS and creates a fresh database only
after the backend authorizes that exact key and request. Existing data is never
replaced, including after interrupted enrollment.
"""
from __future__ import annotations

from contextlib import closing, contextmanager, suppress
from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import ssl
import sys
from urllib.error import HTTPError, URLError
from urllib.request import HTTPSHandler, HTTPRedirectHandler, ProxyHandler, Request, build_opener
from uuid import UUID, uuid4

from sqlalchemy import select

from app.core.config import PRODUCTION_SUPABASE_PROJECT_REF, Settings
from app.core.database import build_engine, build_session_factory
from app.core.installation_identity import (
    InstallationCredentials, InstallationCredentialStore, WindowsDpapiProtector,
)
from app.core.security import verify_password
from app.models import Role, Setting, User
from app.services.admin import AdminService
from app.services.bootstrap import initialize_database
from app.services.system_maintenance import validate_database


_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{7,119}$")
_USERNAME = re.compile(r"^[a-z0-9][a-z0-9_.@+-]{2,159}$")
_HASH = re.compile(r"^scrypt\$16384\$8\$1\$[A-Za-z0-9_-]{22}==\$[A-Za-z0-9_-]{43}=$")
ENDPOINT = f"https://{PRODUCTION_SUPABASE_PROJECT_REF}.supabase.co/functions/v1/erp-activate"


class ActivationError(RuntimeError):
    """Bounded user-safe error; never interpolate transport/payload exceptions."""


def _text(value, maximum=160):
    return isinstance(value, str) and 1 <= len(value.strip()) <= maximum and not any(
        ord(c) < 32 or ord(c) == 127 for c in value
    )


@dataclass(frozen=True)
class ActivationProfile:
    tenant_key: str
    installation_key: str
    company_name: str
    username: str

    def __post_init__(self):
        if (not _IDENTIFIER.fullmatch(self.tenant_key) or len(self.tenant_key) > 80
                or not _IDENTIFIER.fullmatch(self.installation_key)
                or not _text(self.company_name) or not _USERNAME.fullmatch(self.username)):
            raise ActivationError("O pacote de instalação não corresponde à sua empresa. Contate a NexPoint.")

    @classmethod
    def load(cls, path: Path):
        try:
            if path.is_symlink() or path.stat().st_size > 4096:
                raise ValueError()
            value = json.loads(path.read_text(encoding="utf-8-sig"))
            if set(value) != {"schema_version", "tenant_key", "installation_key", "company_name", "username"} or type(value["schema_version"]) is not int or value.pop("schema_version") != 1:
                raise ValueError()
            return cls(**value)
        except (OSError, ValueError, TypeError, KeyError):
            raise ActivationError("Este pacote precisa da identificação da empresa. Contate a NexPoint.") from None


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request_activation(payload: dict) -> dict:
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if len(raw) > 4096:
        raise ActivationError("Confira o login e a senha informados.")
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    opener = build_opener(ProxyHandler({}), HTTPSHandler(context=context), _NoRedirect())
    request = Request(ENDPOINT, data=raw, method="POST", headers={
        "Content-Type": "application/json", "Accept": "application/json",
        "User-Agent": "NexPoint-ERP-Activation/1",
    })
    try:
        with opener.open(request, timeout=20) as response:
            encoded = response.read(16385)
            if response.status != 200 or len(encoded) > 16384:
                raise ValueError()
            result = json.loads(encoded)
            if not isinstance(result, dict):
                raise ValueError()
            return result
    except HTTPError as error:
        status = error.code
        error.close()
        if status in {400, 401, 403}:
            raise ActivationError("Login, senha ou autorização de instalação inválidos. Confira os dados ou contate a NexPoint.") from None
        if status == 409:
            raise ActivationError("Esta autorização já foi usada em outro computador. Contate a NexPoint.") from None
        if status == 429:
            raise ActivationError("Muitas tentativas. Aguarde 15 minutos antes de tentar novamente.") from None
        raise ActivationError("A configuração está indisponível agora. Tente novamente em alguns minutos.") from None
    except (URLError, OSError, ValueError, TimeoutError):
        raise ActivationError("Não foi possível concluir a configuração. Confira a internet e tente novamente. Seus dados serão preservados.") from None


def key_id(credentials: InstallationCredentials) -> str:
    material = f"{credentials.tenant_id}\0{credentials.installation_id}\0{credentials.secret}"
    return "key_" + sha256(b"nexpoint-installation-key-id-v1\0" + material.encode()).hexdigest()


def _regular(path: Path):
    for item in (path, *path.parents):
        if item.is_symlink() or (hasattr(item, "is_junction") and item.is_junction()):
            raise ActivationError("A pasta de configuração não é um diretório local regular. Contate a NexPoint.")


@contextmanager
def _exclusive(path):
    _regular(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise ActivationError("Já existe uma configuração em andamento. Feche a outra janela.") from None
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


class InstallationActivation:
    def __init__(self, settings: Settings, profile: ActivationProfile, *, protector=None, remote=None):
        if settings.environment != "production" or settings.qa_mode or settings.supabase_project_ref != PRODUCTION_SUPABASE_PROJECT_REF:
            raise ActivationError("O pacote de instalação não é válido para uso oficial.")
        self.settings, self.profile = settings, profile
        self.root = Path(settings.data_directory).resolve().parent
        self.database = Path(settings.data_directory) / "erp.sqlite3"
        self.vault = Path(settings.installation_credentials_path)
        if self.vault.resolve().parent != self.root / "credentials":
            raise ActivationError("A pasta de configuração precisa ser conferida pela NexPoint.")
        self.pending = self.root / "credentials" / "activation.dpapi"
        self.protector = protector or WindowsDpapiProtector()
        self.remote = remote or request_activation

    def _save(self, state):
        _regular(self.pending)
        self.pending.parent.mkdir(parents=True, exist_ok=True)
        protected = self.protector.protect(json.dumps(state, ensure_ascii=False).encode())
        temporary = self.pending.with_name(".activation-" + secrets.token_hex(12) + ".tmp")
        try:
            with temporary.open("xb") as stream:
                stream.write(protected)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.pending)
        finally:
            temporary.unlink(missing_ok=True)
        if self._load() != state:
            raise ActivationError("Não foi possível proteger a configuração neste Windows. Contate a NexPoint.")

    def _load(self):
        _regular(self.pending)
        if self.pending.stat().st_size > 32768:
            raise ActivationError("A configuração local precisa ser conferida pela NexPoint. Não apague arquivos.")
        state = json.loads(self.protector.unprotect(self.pending.read_bytes()))
        if (not isinstance(state, dict) or set(state) != {"schema_version", "tenant_key", "installation_key", "request_id", "secret", "phase", "authorized"}
                or state["schema_version"] != 1 or state["phase"] not in {"pending", "authorized", "complete"}
                or state["tenant_key"] != self.profile.tenant_key or state["installation_key"] != self.profile.installation_key
                or UUID(state["request_id"]).version != 4):
            raise ActivationError("A configuração local não corresponde a esta instalação. Contate a NexPoint.")
        InstallationCredentials(state["tenant_key"], state["installation_key"], state["secret"])
        return state

    def _validate_reply(self, reply, state):
        creds = InstallationCredentials(state["tenant_key"], state["installation_key"], state["secret"])
        expected = {"schema_version", "ok", "request_id", "tenant_key", "installation_key", "tenant_kind", "company_name", "installation_label", "environment", "key_id", "username", "display_name", "role", "owner_username", "owner_display_name", "owner_password_hash"}
        if (not isinstance(reply, dict) or set(reply) != expected or reply["schema_version"] != 1 or reply["ok"] is not True
                or reply["request_id"] != state["request_id"] or reply["tenant_key"] != state["tenant_key"]
                or reply["installation_key"] != state["installation_key"] or reply["key_id"] != key_id(creds)
                or reply["environment"] != "prod" or reply["role"] != "user"
                or reply["tenant_kind"] not in {"internal", "customer"} or reply["username"] != self.profile.username
                or reply["company_name"] != self.profile.company_name
                or any(not _text(reply[k]) for k in ("display_name", "company_name", "installation_label", "owner_display_name"))
                or not isinstance(reply["owner_username"], str) or not _USERNAME.fullmatch(reply["owner_username"])
                or reply["owner_username"] in {reply["username"], "nexpoint-admin"}
                or not isinstance(reply["owner_password_hash"], str) or not _HASH.fullmatch(reply["owner_password_hash"])):
            raise ActivationError("A autorização recebida não corresponde a este computador. Contate a NexPoint.")

    def _validate_database(self, reply, state, password):
        validate_database(self.database, require_latest=True)
        with closing(sqlite3.connect(self.database.as_uri() + "?mode=ro", uri=True)) as db:
            marker = db.execute("select value from settings where key='system.activation_request_id'").fetchone()
            company = db.execute("select value from settings where key='company.name'").fetchone()
            user = db.execute("select u.password_hash, r.code from users u join user_roles ur on ur.user_id=u.id join roles r on r.id=ur.role_id where u.email=? and u.active=1", (reply["username"],)).fetchall()
            owner = db.execute("select u.password_hash from users u join user_roles ur on ur.user_id=u.id join roles r on r.id=ur.role_id where u.email=? and u.active=1 and r.code='admin'", (reply["owner_username"],)).fetchall()
            if (marker != (state["request_id"],) or company != (reply["company_name"],) or len(user) != 1
                    or user[0][1] != "user" or not verify_password(password, user[0][0])
                    or owner != [(reply["owner_password_hash"],)]):
                raise ActivationError("Já existem dados neste computador que precisam ser conferidos. Nada foi substituído.")

    def _create_database(self, reply, state, password):
        _regular(self.database)
        if self.database.exists():
            self._validate_database(reply, state, password)
            return
        self.database.parent.mkdir(parents=True, exist_ok=True)
        stage = self.database.with_name(".activation-" + secrets.token_hex(12) + ".sqlite3")
        engine = None
        try:
            with stage.open("xb"):
                pass
            from dataclasses import replace
            settings = replace(self.settings, database_url="sqlite+pysqlite:///" + stage.as_posix(),
                               company_name=reply["company_name"], tenant_type=reply["tenant_kind"].upper())
            engine = build_engine(settings.database_url)
            factory = build_session_factory(engine)
            initialize_database(engine, factory, {}, settings, require_migration_backup=False)
            with factory() as session:
                role = session.scalar(select(Role).where(Role.code == "admin"))
                owner = User(email=reply["owner_username"], display_name=reply["owner_display_name"],
                             password_hash=reply["owner_password_hash"], active=True, roles=[role])
                session.add(owner)
                session.add(Setting(key="system.activation_request_id", value=state["request_id"]))
                session.commit()
                owner_id = owner.id
            with factory() as session:
                AdminService(session).create_user({"email": reply["username"], "display_name": reply["display_name"], "password": password}, {"user"}, owner_id)
            engine.dispose()
            engine = None
            validate_database(stage, require_latest=True)
            with closing(sqlite3.connect(stage)) as db:
                if any(db.execute(f"select count(*) from {table}").fetchone()[0] for table in ("customers", "services", "service_notes", "payments", "cash_movements", "outbox_items")):
                    raise ActivationError("A configuração inicial não passou na verificação. Contate a NexPoint.")
            os.link(stage, self.database)  # create-only, including concurrent destination creation
            self._validate_database(reply, state, password)
        finally:
            if engine is not None:
                engine.dispose()
            with suppress(OSError):
                stage.unlink(missing_ok=True)

    def activate(self, username: str, password: str):
        if username.strip().lower() != self.profile.username or not 8 <= len(password) <= 256 or "\0" in password:
            raise ActivationError("Confira o login e a senha informados.")
        try:
            with _exclusive(self.root / "credentials" / "activation.lock"):
                if self.pending.exists():
                    state = self._load()
                else:
                    if self.vault.exists() or self.database.exists():
                        raise ActivationError("Este computador já possui uma instalação. Contate a NexPoint; nenhum dado foi substituído.")
                    state = {"schema_version": 1, "tenant_key": self.profile.tenant_key,
                             "installation_key": self.profile.installation_key, "request_id": str(uuid4()),
                             "secret": secrets.token_urlsafe(48), "phase": "pending", "authorized": None}
                    self._save(state)
                creds = InstallationCredentials(state["tenant_key"], state["installation_key"], state["secret"])
                if state["phase"] == "pending":
                    reply = self.remote({"schema_version": 1, "username": self.profile.username,
                        "password": password, "tenant_key": self.profile.tenant_key,
                        "installation_key": self.profile.installation_key, "environment": "prod",
                        "request_id": state["request_id"], "key_id": key_id(creds),
                        "secret_digest": sha256(creds.secret.encode()).hexdigest()})
                    self._validate_reply(reply, state)
                    # Store the authorized login hash so an offline retry cannot
                    # choose a new operational password after authorization.
                    from app.core.security import hash_password
                    state["authorized"] = {"reply": reply, "login_hash": hash_password(password)}
                    state["phase"] = "authorized"
                    self._save(state)
                authorized = state["authorized"]
                reply = authorized["reply"]
                self._validate_reply(reply, state)
                if not verify_password(password, authorized["login_hash"]):
                    raise ActivationError("Confira o login e a senha informados.")
                self._create_database(reply, state, password)
                config = {"schema_version": 1, "company_name": reply["company_name"],
                          "tenant_type": reply["tenant_kind"].upper(), "supabase_project_ref": PRODUCTION_SUPABASE_PROJECT_REF}
                config_path = self.root / "installation.json"
                _regular(config_path)
                if config_path.exists():
                    if json.loads(config_path.read_text(encoding="utf-8-sig")) != config:
                        raise ActivationError("A configuração existente precisa ser conferida. Nada foi substituído.")
                else:
                    with config_path.open("x", encoding="utf-8") as stream:
                        json.dump(config, stream, ensure_ascii=False)
                creds = InstallationCredentials(creds.tenant_id, creds.installation_id, creds.secret, reply["tenant_kind"])
                _regular(self.vault)
                store = InstallationCredentialStore(self.vault, protector=self.protector)
                if self.vault.exists():
                    previous = store.load()
                    if (previous.tenant_id, previous.installation_id, previous.secret, previous.tenant_kind) != (creds.tenant_id, creds.installation_id, creds.secret, creds.tenant_kind):
                        raise ActivationError("A identidade existente é diferente. Nenhuma credencial foi substituída.")
                else:
                    store.store(creds)
                if store.load().secret != creds.secret:
                    raise ActivationError("Não foi possível proteger a configuração. Contate a NexPoint.")
                state["phase"] = "complete"
                self._save(state)
                return reply
        except ActivationError:
            raise
        except Exception:
            raise ActivationError("Não foi possível concluir a configuração neste Windows. Não apague arquivos; tente novamente ou contate a NexPoint.") from None


def needs_activation(settings: Settings) -> bool:
    if settings.environment != "production":
        return False
    vault = Path(settings.installation_credentials_path)
    pending = vault.with_name("activation.dpapi")
    # Completed/legacy installations remain fully offline-first. Corrupt data is
    # handled by normal startup; its existence must never cause an automatic reset.
    if vault.exists() and (Path(settings.data_directory) / "erp.sqlite3").exists():
        return False
    return not vault.exists() or pending.exists()


def installed_profile() -> ActivationProfile:
    root = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[2]
    return ActivationProfile.load(root / "activation-profile.json")
