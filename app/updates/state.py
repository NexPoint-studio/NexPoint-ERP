"""Atomic, authenticated local update journal. No URLs or secrets are persisted."""
from __future__ import annotations

from contextlib import suppress
from hashlib import sha256
import hmac
import json
import os
from pathlib import Path
import secrets
import time
from threading import RLock

from app.core.installation_identity import InstallationCredentials
from app.updates.contracts import ReleaseManifest, UPDATE_CHANNELS, UpdateValidationError
from app.updates.download import _safe_directory

STATES = frozenset({"IDLE", "CHECKING", "CURRENT", "AVAILABLE", "DOWNLOADING", "READY",
                    "INSTALLING", "RESTARTING", "SUCCEEDED", "FAILED", "ROLLED_BACK", "ROLLBACK_REQUIRED"})
ERRORS = frozenset({None, "OFFLINE", "INVALID_RELEASE", "DOWNLOAD_FAILED", "INSTALL_FAILED",
                    "INSUFFICIENT_SPACE", "APP_RUNNING", "HEALTH_FAILED", "SCHEMA_CHANGED", "CANCELLED", "RESTART_FAILED"})


def initial_state(installation_id: str) -> dict:
    return {"schema_version": 1, "installation_id": installation_id, "state": "IDLE", "update_channel": None,
            "highest_sequences": {channel: 0 for channel in sorted(UPDATE_CHANNELS)},
            "last_checked_at": 0, "state_changed_at": 0, "release": None, "error_code": None}


class UpdateJournal:
    def __init__(self, directory: Path, credentials: InstallationCredentials):
        self.directory = _safe_directory(directory)
        self.path = self.directory / "state.json"
        self._installation = credentials.installation_id
        self._key = hmac.new(credentials.secret.encode(),
                             f"nexpoint-local-update-journal-v1\n{self._installation}".encode(), sha256).digest()
        self._lock = RLock()

    def _validate(self, value: object) -> dict:
        if (not isinstance(value, dict) or set(value) != set(initial_state(self._installation))
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
            or value["installation_id"] != self._installation
            or not isinstance(value["state"], str) or value["state"] not in STATES
            or (value["update_channel"] is not None and
                (not isinstance(value["update_channel"], str) or value["update_channel"] not in UPDATE_CHANNELS))
            or type(value["last_checked_at"]) is not int or not 0 <= value["last_checked_at"] < 2**53
            or type(value["state_changed_at"]) is not int or not 0 <= value["state_changed_at"] < 2**53
            or (value["error_code"] is not None and
                (not isinstance(value["error_code"], str) or value["error_code"] not in ERRORS))):
            raise UpdateValidationError("O estado local da atualização é inválido.")
        sequences = value["highest_sequences"]
        if (not isinstance(sequences, dict) or set(sequences) != UPDATE_CHANNELS
            or any(type(n) is not int or not 0 <= n < 2**53 for n in sequences.values())):
            raise UpdateValidationError("O histórico de versões é inválido.")
        if value["release"] is not None:
            manifest = ReleaseManifest.parse(value["release"])
            if value["update_channel"] != manifest.update_channel or sequences[manifest.update_channel] != manifest.sequence:
                raise UpdateValidationError("O histórico diverge da versão autorizada.")
        elif value["state"] in {"AVAILABLE", "DOWNLOADING", "READY", "INSTALLING", "RESTARTING", "SUCCEEDED"}:
            raise UpdateValidationError("A versão autorizada está ausente.")
        # A defensive copy ensures callers cannot mutate the in-memory result
        # after validation or smuggle extra objects into serialization.
        return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))

    def load(self) -> dict:
        with self._lock:
            if not self.path.exists():
                return initial_state(self._installation)
            try:
                if self.path.is_symlink() or not self.path.is_file() or self.path.stat().st_size > 65536:
                    raise ValueError
                envelope = json.loads(self.path.read_bytes())
                if not isinstance(envelope, dict) or set(envelope) != {"payload", "signature"}:
                    raise ValueError
                encoded = json.dumps(envelope["payload"], ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode()
                expected = hmac.new(self._key, encoded, sha256).hexdigest()
                if not isinstance(envelope["signature"], str) or not hmac.compare_digest(expected, envelope["signature"]):
                    raise ValueError
                return self._validate(envelope["payload"])
            except Exception:
                raise UpdateValidationError("O histórico local de atualização não pôde ser validado. Seus dados foram preservados.") from None

    def save(self, value: dict) -> dict:
        with self._lock:
            candidate = self._validate(value)
            previous = self.load()
            if any(candidate[key] != previous[key] for key in ("state", "update_channel", "release", "error_code")):
                candidate["state_changed_at"] = max(time.time_ns() // 1_000_000, previous["state_changed_at"] + 1)
            else:
                candidate["state_changed_at"] = previous["state_changed_at"]
            if any(candidate["highest_sequences"][c] < previous["highest_sequences"][c] for c in UPDATE_CHANNELS):
                raise UpdateValidationError("O histórico não pode voltar a uma versão anterior.")
            if previous["release"] is not None and candidate["release"] is not None:
                old, new = previous["release"], candidate["release"]
                if old["update_channel"] == new["update_channel"] and old["sequence"] == new["sequence"] and old != new:
                    raise UpdateValidationError("O conteúdo de uma versão autorizada não pode mudar.")
            encoded = json.dumps(candidate, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
            envelope = json.dumps({"payload": candidate, "signature": hmac.new(self._key, encoded, sha256).hexdigest()},
                                  ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
            if len(envelope) > 65536:
                raise UpdateValidationError("O histórico de atualização excedeu o limite.")
            temporary = self.directory / f".state-{secrets.token_hex(16)}.partial"
            try:
                with temporary.open("xb") as stream:
                    stream.write(envelope)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.path)
            except OSError:
                raise UpdateValidationError("Não foi possível salvar o estado da atualização.") from None
            finally:
                with suppress(OSError):
                    temporary.unlink(missing_ok=True)
            return candidate
