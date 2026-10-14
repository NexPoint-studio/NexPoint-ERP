"""One low-frequency worker; network and downloads stay off HTTP/UI threads."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from threading import Event, Lock, Thread
import copy
import time

from app.services.system_info import UpdateStatus
from app.updates.contracts import ReleaseManifest, UpdateValidationError
from app.updates.download import download_release
from app.updates.state import UpdateJournal
from app.updates.verification import verify_installable_artifact

BUSY = frozenset({"CHECKING", "DOWNLOADING", "INSTALLING", "RESTARTING"})
MESSAGES = {
    "IDLE": ("Aguardando verificação", "A verificação ocorre em segundo plano."),
    "CHECKING": ("Verificando atualizações", "Você pode continuar usando o ERP."),
    "CURRENT": ("ERP atualizado", "Nenhuma versão mais recente foi publicada para este canal."),
    "AVAILABLE": ("Nova versão disponível", "Confira as melhorias e escolha quando atualizar."),
    "DOWNLOADING": ("Baixando atualização", "O ERP continua disponível durante o download."),
    "READY": ("Atualização pronta", "Salve seu trabalho. A atualização fechará e abrirá o ERP novamente."),
    "INSTALLING": ("Instalando atualização", "Aguarde a conclusão da instalação."),
    "RESTARTING": ("Validando atualização", "Conferindo a versão e a integridade da instalação."),
    "SUCCEEDED": ("Atualização concluída", "A nova versão foi iniciada e verificada."),
    "FAILED": ("Atualização não concluída", "Seus dados foram preservados. Você pode tentar novamente."),
    "ROLLED_BACK": ("Versão anterior recuperada", "A nova versão não iniciou corretamente. Contate a NexPoint."),
    "ROLLBACK_REQUIRED": ("Recuperação necessária", "Não reinstale nem apague dados. Contate a NexPoint."),
}


class UpdateManager:
    def __init__(self, journal: UpdateJournal, remote, installed_version: str, *,
                 downloader=download_release, verifier=verify_installable_artifact,
                 clock=time.time, startup_delay=15, interval=21600):
        self.journal, self.remote, self.installed_version = journal, remote, installed_version
        self._downloader, self._verifier, self._clock = downloader, verifier, clock
        self._startup_delay, self._interval = max(0, startup_delay), max(3600, interval)
        self._stop, self._wake, self._mutex = Event(), Event(), Lock()
        self._thread, self._command, self._last_request = None, None, 0.0
        self._state = journal.load()
        if self._state["state"] in {"CHECKING", "DOWNLOADING"}:
            self._state = journal.save(self._state | {"state": "FAILED", "error_code": "CANCELLED"})

    def start(self):
        with self._mutex:
            if self._thread is None or not self._thread.is_alive():
                self._stop.clear()
                self._wake.clear()
                self._thread = Thread(target=self._run, name="nexpoint-update-check", daemon=True)
                self._thread.start()

    def stop(self):
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=6)

    def _run(self):
        if self._stop.wait(self._startup_delay):
            return
        # Download only in the background worker. Installation still requires
        # the separate, explicit desktop confirmation and updater handoff.
        self._execute("download")
        while not self._stop.is_set():
            manual = self._wake.wait(self._interval)
            self._wake.clear()
            if self._stop.is_set():
                return
            with self._mutex:
                command, self._command = self._command, None
            self._execute(command if manual and command else "download")

    def request(self, command: str) -> bool:
        if command not in {"check", "download"}:
            raise UpdateValidationError("A ação de atualização é inválida.")
        with self._mutex:
            if self._state["state"] in BUSY or self._command is not None:
                return False
            if command == "check" and self._clock() - self._last_request < 60:
                return False
            self._last_request, self._command = self._clock(), command
            self._wake.set()
            return True

    def snapshot(self) -> dict:
        with self._mutex:
            return copy.deepcopy(self._state)

    def _save(self, **changes):
        with self._mutex:
            self._state = self.journal.save(self._state | changes)

    def _execute(self, command: str):
        previous = self.snapshot()
        if previous["state"] in {"INSTALLING", "RESTARTING", "ROLLBACK_REQUIRED"}:
            return
        if command == "check" and previous["state"] == "READY":
            return
        try:
            self._save(state="CHECKING", error_code=None, last_attempt_at=int(self._clock() * 1000))
            result = self.remote.check(self.installed_version)
            if self._stop.is_set():
                self._save(state=previous["state"], error_code="CANCELLED")
                return
            sequences = dict(previous["highest_sequences"])
            if result.release is None:
                self._save(state="CURRENT", release=None, update_channel=result.update_channel,
                           last_checked_at=int(self._clock()), error_code=None)
                return
            release = result.release
            available = release.check_for_installation(installed_version=self.installed_version,
                channel=result.update_channel, highest_sequence=sequences[result.update_channel],
                now=datetime.fromtimestamp(self._clock(), timezone.utc))
            sequences[result.update_channel] = release.sequence
            if not available:
                self._save(state="CURRENT", release=None, update_channel=result.update_channel,
                           highest_sequences=sequences, last_checked_at=int(self._clock()), error_code=None)
                return
            self._save(state="AVAILABLE", release=release.to_dict(), update_channel=result.update_channel,
                       highest_sequences=sequences, last_checked_at=int(self._clock()), error_code=None)
            if command == "download":
                if result.download is None:
                    raise UpdateValidationError("A autorização de download está ausente.")
                self._save(state="DOWNLOADING")
                path = self._downloader(result.download, self.journal.directory / "downloads")
                try:
                    self._verifier(path, release)
                except Exception:
                    expected = self.journal.directory / "downloads" / f"{release.artifact_sha256}.exe"
                    if path == expected and path.is_file() and not path.is_symlink():
                        path.unlink()
                    raise
                self._save(state="READY", error_code=None)
        except Exception:
            current = self.snapshot()
            code = "DOWNLOAD_FAILED" if current["state"] == "DOWNLOADING" else "OFFLINE"
            fallback = previous["state"] if previous["state"] in {"AVAILABLE", "READY", "CURRENT", "SUCCEEDED"} else "FAILED"
            try:
                self._save(state=fallback, release=previous["release"], error_code=code,
                           update_channel=previous["update_channel"])
            except UpdateValidationError:
                self._save(state="FAILED", error_code=code)

    def status(self, installed_version: str) -> UpdateStatus:
        state = self.snapshot()
        label, message = MESSAGES[state["state"]]
        release = state["release"]
        if state["error_code"] == "OFFLINE":
            message = "Não foi possível consultar atualizações agora. O ERP continua disponível."
        elif state["error_code"] == "DOWNLOAD_FAILED":
            message = "Não foi possível validar o download. Nada foi instalado. Tente novamente."
        elif state["error_code"] == "INSUFFICIENT_SPACE":
            message = "Não há espaço livre suficiente para instalar e guardar a recuperação. Libere espaço e tente novamente."
        elif state["error_code"] == "APP_RUNNING":
            message = "O ERP ainda estava aberto. Feche as outras janelas antes de tentar atualizar novamente."
        elif state["error_code"] == "RESTART_FAILED":
            message = "O Windows não conseguiu reabrir o ERP após a verificação. Abra o aplicativo pelo atalho; seus dados foram preservados."
        if release and release["mandatory"] and state["state"] in {"AVAILABLE", "READY"}:
            message = "Esta versão precisa ser atualizada para continuar. Salve seu trabalho antes de atualizar."
        return UpdateStatus(state["state"], label, message, release["version"] if release else None)

    def ready_artifact(self) -> tuple[Path, ReleaseManifest]:
        state = self.snapshot()
        if state["state"] != "READY" or state["release"] is None:
            raise UpdateValidationError("A atualização ainda não está pronta para instalar.")
        manifest = ReleaseManifest.parse(state["release"])
        path = self.journal.directory / "downloads" / f"{manifest.artifact_sha256}.exe"
        self._verifier(path, manifest)
        return path, manifest
