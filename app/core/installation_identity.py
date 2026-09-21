from __future__ import annotations

import base64
import binascii
import ctypes
from ctypes import wintypes
from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
import hmac
import json
import os
from pathlib import Path
import re
import secrets
from typing import Protocol


_FILE_MAGIC = b"NEXPOINT-ERP-INSTALLATION-V1\n"
_MAX_FILE_BYTES = 64 * 1024
_TENANT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{7,79}$")
_INSTALLATION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{7,119}$")
_INSTALLATION_SECRET = re.compile(r"^[A-Za-z0-9_-]{43,256}$")
_UTC_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?\+00:00$")
_ENTROPY = b"NexPoint ERP installation credential v1"


class InstallationCredentialError(RuntimeError):
    """A safe, non-secret failure while reading installation credentials."""


class DataProtector(Protocol):
    def protect(self, plaintext: bytes) -> bytes: ...

    def unprotect(self, ciphertext: bytes) -> bytes: ...


@dataclass(frozen=True, slots=True)
class InstallationCredentials:
    tenant_id: str
    installation_id: str
    secret: str = field(repr=False)
    tenant_kind: str = "CUSTOMER"
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def __post_init__(self) -> None:
        if not _TENANT_ID.fullmatch(self.tenant_id):
            raise InstallationCredentialError("tenant_id da instalacao e invalido.")
        if not _INSTALLATION_ID.fullmatch(self.installation_id):
            raise InstallationCredentialError("installation_id da instalacao e invalido.")
        if not _INSTALLATION_SECRET.fullmatch(self.secret):
            raise InstallationCredentialError("A credencial da instalacao e invalida.")
        normalized_kind = str(self.tenant_kind or "").strip().upper()
        if normalized_kind not in {"CUSTOMER", "INTERNAL"}:
            raise InstallationCredentialError("O tipo do tenant da instalacao e invalido.")
        object.__setattr__(self, "tenant_kind", normalized_kind)
        if not _UTC_TIMESTAMP.fullmatch(self.created_at):
            raise InstallationCredentialError("A data da credencial da instalacao e invalida.")

    def local_session_secret(self) -> str:
        """Derive a local-only key without persisting another plaintext secret."""

        context = (
            "nexpoint-erp-local-session-v1\n"
            f"{self.tenant_id}\n{self.installation_id}"
        ).encode("utf-8")
        return hmac.new(self.secret.encode("utf-8"), context, sha256).hexdigest()


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


def _input_blob(value: bytes) -> tuple[_DataBlob, object]:
    buffer = (ctypes.c_ubyte * len(value)).from_buffer_copy(value)
    return _DataBlob(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))), buffer


class WindowsDpapiProtector:
    """Protect installation material for the current Windows user with DPAPI."""

    _CRYPTPROTECT_UI_FORBIDDEN = 0x1

    def __init__(self) -> None:
        if os.name != "nt":
            raise InstallationCredentialError(
                "A credencial PROD exige protecao DPAPI do Windows."
            )

    @staticmethod
    def _libraries():
        try:
            crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        except (AttributeError, OSError) as exc:
            raise InstallationCredentialError("DPAPI do Windows indisponivel.") from exc
        crypt32.CryptProtectData.argtypes = [
            ctypes.POINTER(_DataBlob),
            wintypes.LPCWSTR,
            ctypes.POINTER(_DataBlob),
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(_DataBlob),
        ]
        crypt32.CryptProtectData.restype = wintypes.BOOL
        crypt32.CryptUnprotectData.argtypes = [
            ctypes.POINTER(_DataBlob),
            ctypes.c_void_p,
            ctypes.POINTER(_DataBlob),
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(_DataBlob),
        ]
        crypt32.CryptUnprotectData.restype = wintypes.BOOL
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree.restype = ctypes.c_void_p
        return crypt32, kernel32

    def protect(self, plaintext: bytes) -> bytes:
        if not plaintext:
            raise InstallationCredentialError("A credencial da instalacao esta vazia.")
        crypt32, kernel32 = self._libraries()
        source, source_buffer = _input_blob(plaintext)
        entropy, entropy_buffer = _input_blob(_ENTROPY)
        destination = _DataBlob()
        if not crypt32.CryptProtectData(
            ctypes.byref(source),
            "NexPoint ERP installation credential",
            ctypes.byref(entropy),
            None,
            None,
            self._CRYPTPROTECT_UI_FORBIDDEN,
            ctypes.byref(destination),
        ):
            raise InstallationCredentialError("DPAPI nao protegeu a credencial da instalacao.")
        del source_buffer, entropy_buffer
        try:
            return ctypes.string_at(destination.pbData, destination.cbData)
        finally:
            kernel32.LocalFree(destination.pbData)

    def unprotect(self, ciphertext: bytes) -> bytes:
        if not ciphertext:
            raise InstallationCredentialError("O arquivo de credencial esta vazio.")
        crypt32, kernel32 = self._libraries()
        source, source_buffer = _input_blob(ciphertext)
        entropy, entropy_buffer = _input_blob(_ENTROPY)
        destination = _DataBlob()
        if not crypt32.CryptUnprotectData(
            ctypes.byref(source),
            None,
            ctypes.byref(entropy),
            None,
            None,
            self._CRYPTPROTECT_UI_FORBIDDEN,
            ctypes.byref(destination),
        ):
            raise InstallationCredentialError(
                "A credencial da instalacao nao pertence a este usuario Windows ou foi corrompida."
            )
        del source_buffer, entropy_buffer
        try:
            return ctypes.string_at(destination.pbData, destination.cbData)
        finally:
            kernel32.LocalFree(destination.pbData)


class InstallationCredentialStore:
    """Atomic DPAPI-backed storage; plaintext never reaches disk or logs."""

    def __init__(self, path: Path, *, protector: DataProtector | None = None):
        self.path = Path(path).expanduser().resolve()
        self.protector = protector or WindowsDpapiProtector()

    @staticmethod
    def _serialize(credentials: InstallationCredentials) -> bytes:
        payload = {
            "schema_version": 1,
            "tenant_id": credentials.tenant_id,
            "installation_id": credentials.installation_id,
            "secret": credentials.secret,
            "tenant_kind": credentials.tenant_kind,
            "created_at": credentials.created_at,
        }
        return json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")

    @staticmethod
    def _deserialize(plaintext: bytes) -> InstallationCredentials:
        try:
            decoded = json.loads(plaintext.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise InstallationCredentialError("A credencial da instalacao foi corrompida.") from exc
        expected = {
            "schema_version", "tenant_id", "installation_id", "secret",
            "tenant_kind", "created_at",
        }
        if not isinstance(decoded, dict) or set(decoded) != expected or decoded.get("schema_version") != 1:
            raise InstallationCredentialError("O contrato da credencial da instalacao e invalido.")
        if not all(
            isinstance(decoded.get(key), str)
            for key in (
                "tenant_id", "installation_id", "secret", "tenant_kind", "created_at"
            )
        ):
            raise InstallationCredentialError("O contrato da credencial da instalacao e invalido.")
        try:
            return InstallationCredentials(
                tenant_id=str(decoded["tenant_id"]),
                installation_id=str(decoded["installation_id"]),
                secret=str(decoded["secret"]),
                tenant_kind=str(decoded["tenant_kind"]),
                created_at=str(decoded["created_at"]),
            )
        except (KeyError, TypeError) as exc:
            raise InstallationCredentialError("O contrato da credencial da instalacao e invalido.") from exc

    def store(self, credentials: InstallationCredentials) -> None:
        if self.path.exists() and self.path.is_symlink():
            raise InstallationCredentialError("O arquivo de credencial nao pode ser um link.")
        try:
            ciphertext = self.protector.protect(self._serialize(credentials))
            encoded = _FILE_MAGIC + base64.b64encode(ciphertext)
            if len(encoded) > _MAX_FILE_BYTES:
                raise InstallationCredentialError("A credencial protegida excede o limite permitido.")
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_name(
                f".{self.path.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
            )
            try:
                with temporary.open("xb") as stream:
                    stream.write(encoded)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.path)
            finally:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
        except InstallationCredentialError:
            raise
        except OSError as exc:
            raise InstallationCredentialError(
                "Nao foi possivel salvar a credencial protegida da instalacao."
            ) from exc

    def load(self) -> InstallationCredentials:
        if not self.path.is_file() or self.path.is_symlink():
            raise InstallationCredentialError(
                "A instalacao PROD ainda nao possui credencial protegida."
            )
        try:
            size = self.path.stat().st_size
            if size <= len(_FILE_MAGIC) or size > _MAX_FILE_BYTES:
                raise InstallationCredentialError("O arquivo de credencial e invalido.")
            encoded = self.path.read_bytes()
            if not encoded.startswith(_FILE_MAGIC):
                raise InstallationCredentialError("O arquivo de credencial e invalido.")
            try:
                ciphertext = base64.b64decode(
                    encoded[len(_FILE_MAGIC):], validate=True
                )
            except (ValueError, binascii.Error) as exc:
                raise InstallationCredentialError("O arquivo de credencial e invalido.") from exc
            plaintext = self.protector.unprotect(ciphertext)
            try:
                return self._deserialize(plaintext)
            finally:
                # Python bytes cannot be reliably zeroed; keep its lifetime as short as possible.
                del plaintext
        except InstallationCredentialError:
            raise
        except OSError as exc:
            raise InstallationCredentialError(
                "Nao foi possivel ler a credencial protegida da instalacao."
            ) from exc
