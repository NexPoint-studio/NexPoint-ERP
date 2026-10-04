from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import re
from urllib.parse import parse_qs, urlsplit
from uuid import UUID


MAX_ARTIFACT_BYTES = 512 * 1024 * 1024
UPDATE_CHANNELS = frozenset({"stable", "beta", "internal"})
RELEASE_BUCKET = "nexpoint-releases"
_VERSION = re.compile(r"(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})\Z")
_HASH = re.compile(r"[a-f0-9]{64}\Z")


class UpdateValidationError(ValueError):
    """Bounded messages only; never include URLs, tokens or remote payloads."""


def version_parts(value: object) -> tuple[int, int, int]:
    if not isinstance(value, str) or not (match := _VERSION.fullmatch(value)):
        raise UpdateValidationError("A versão da atualização é inválida.")
    return tuple(int(part) for part in match.groups())


def timestamp(value: object) -> datetime:
    if not isinstance(value, str) or len(value) > 40:
        raise UpdateValidationError("O horário da atualização é inválido.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError
        return parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        raise UpdateValidationError("O horário da atualização é inválido.") from None


@dataclass(frozen=True, slots=True)
class ReleaseManifest:
    release_id: str
    sequence: int
    version: str
    update_channel: str
    build_commit: str
    artifact_sha256: str
    artifact_size: int
    release_notes: str
    minimum_supported_version: str
    mandatory: bool
    published_at: datetime
    code_signing: str
    signer_thumbprint: str | None = None
    platform: str = "windows"
    architecture: str = "x64"

    @property
    def object_path(self) -> str:
        return f"windows/x64/{self.artifact_sha256}.exe"

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        result["schema_version"] = 1
        result["published_at"] = self.published_at.isoformat()
        return result

    @classmethod
    def parse(cls, raw: object) -> ReleaseManifest:
        fields = {
            "schema_version", "release_id", "sequence", "version", "update_channel",
            "platform", "architecture", "build_commit", "artifact_sha256", "artifact_size",
            "release_notes", "minimum_supported_version", "mandatory", "published_at",
            "code_signing", "signer_thumbprint",
        }
        if not isinstance(raw, dict) or set(raw) != fields or type(raw["schema_version"]) is not int or raw["schema_version"] != 1:
            raise UpdateValidationError("O contrato da atualização é inválido.")
        try:
            identifier = str(UUID(raw["release_id"]))
        except (ValueError, TypeError, AttributeError):
            raise UpdateValidationError("A identificação da atualização é inválida.") from None
        if identifier != raw["release_id"]:
            raise UpdateValidationError("A identificação da atualização é inválida.")
        current = version_parts(raw["version"])
        minimum = version_parts(raw["minimum_supported_version"])
        if minimum > current:
            raise UpdateValidationError("A versão mínima da atualização é inválida.")
        if not isinstance(raw["update_channel"], str) or raw["update_channel"] not in UPDATE_CHANNELS or raw["platform"] != "windows" or raw["architecture"] != "x64":
            raise UpdateValidationError("A atualização não é compatível com esta instalação.")
        if not isinstance(raw["build_commit"], str) or not re.fullmatch(r"[a-f0-9]{40}", raw["build_commit"]):
            raise UpdateValidationError("A identificação da build é inválida.")
        if not isinstance(raw["artifact_sha256"], str) or not _HASH.fullmatch(raw["artifact_sha256"]):
            raise UpdateValidationError("O hash da atualização é inválido.")
        if type(raw["artifact_size"]) is not int or not 1 <= raw["artifact_size"] <= MAX_ARTIFACT_BYTES:
            raise UpdateValidationError("O tamanho da atualização é inválido.")
        if type(raw["sequence"]) is not int or not 1 <= raw["sequence"] < 2**53:
            raise UpdateValidationError("A sequência da atualização é inválida.")
        if type(raw["mandatory"]) is not bool:
            raise UpdateValidationError("A política da atualização é inválida.")
        notes = raw["release_notes"]
        if not isinstance(notes, str) or not 1 <= len(notes) <= 4000 or any(ord(c) < 32 and c not in "\n\r\t" for c in notes):
            raise UpdateValidationError("O resumo da atualização é inválido.")
        signing, thumbprint = raw["code_signing"], raw["signer_thumbprint"]
        if signing == "CODE_SIGNING_PENDING":
            if thumbprint is not None:
                raise UpdateValidationError("Os dados de assinatura são inconsistentes.")
        elif signing == "AUTHENTICODE":
            if not isinstance(thumbprint, str) or not re.fullmatch(r"(?:[A-F0-9]{40}|[A-F0-9]{64})", thumbprint):
                raise UpdateValidationError("O certificado da atualização é inválido.")
        else:
            raise UpdateValidationError("O estado da assinatura é inválido.")
        return cls(
            release_id=identifier, sequence=raw["sequence"], version=raw["version"],
            update_channel=raw["update_channel"], build_commit=raw["build_commit"],
            artifact_sha256=raw["artifact_sha256"], artifact_size=raw["artifact_size"],
            release_notes=notes, minimum_supported_version=raw["minimum_supported_version"],
            mandatory=raw["mandatory"], published_at=timestamp(raw["published_at"]),
            code_signing=signing, signer_thumbprint=thumbprint,
        )

    def check_for_installation(self, *, installed_version: str, channel: str,
                               highest_sequence: int = 0, now: datetime | None = None) -> bool:
        instant = now or datetime.now(timezone.utc)
        if self.update_channel != channel or self.sequence < highest_sequence:
            raise UpdateValidationError("A atualização não pertence ao canal ou está desatualizada.")
        if self.published_at.timestamp() > instant.timestamp() + 300:
            raise UpdateValidationError("A publicação da atualização ainda não é válida.")
        installed = version_parts(installed_version)
        target = version_parts(self.version)
        if target < installed:
            raise UpdateValidationError("Não é permitido instalar uma versão anterior.")
        return target > installed


@dataclass(frozen=True, slots=True)
class ReleaseDownload:
    manifest: ReleaseManifest
    url: str = field(repr=False)
    expires_at: datetime

    @classmethod
    def parse(cls, manifest: ReleaseManifest, raw: object, *, project_ref: str,
              now: datetime | None = None) -> ReleaseDownload:
        if not re.fullmatch(r"[a-z0-9]{20}", project_ref):
            raise UpdateValidationError("A origem oficial da atualização é inválida.")
        if not isinstance(raw, dict) or set(raw) != {"url", "expires_at"}:
            raise UpdateValidationError("A autorização de download é inválida.")
        url = raw["url"]
        if not isinstance(url, str) or not 1 <= len(url) <= 8192 or any(ord(c) <= 32 or ord(c) >= 127 for c in url):
            raise UpdateValidationError("A autorização de download é inválida.")
        try:
            parsed = urlsplit(url)
            query = parse_qs(parsed.query, strict_parsing=True)
            valid = (
                parsed.scheme == "https" and parsed.netloc == f"{project_ref}.supabase.co"
                and parsed.port is None and parsed.username is None and parsed.password is None
                and not parsed.fragment and parsed.path == f"/storage/v1/object/sign/{RELEASE_BUCKET}/{manifest.object_path}"
                and set(query) == {"token"} and len(query["token"]) == 1
                and re.fullmatch(r"[A-Za-z0-9_.-]{20,7000}", query["token"][0])
            )
        except ValueError:
            valid = False
        if not valid:
            raise UpdateValidationError("O download não pertence à origem oficial.")
        expiry = timestamp(raw["expires_at"])
        seconds = (expiry - (now or datetime.now(timezone.utc))).total_seconds()
        if not 0 < seconds <= 1800:
            raise UpdateValidationError("A autorização de download expirou ou é inválida.")
        return cls(manifest, url, expiry)
