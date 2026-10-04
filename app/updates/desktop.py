"""Windows desktop handoff. No browser-supplied executable or command line."""
from __future__ import annotations

from hashlib import sha256
import hmac
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import time

from app.core.installation_identity import InstallationCredentialStore, WindowsDpapiProtector
from app.updates.contracts import ReleaseManifest, UpdateValidationError, version_parts
from app.updates.installer import UpdateTransaction, file_digest, regular, schema_snapshot
from app.updates.state import UpdateJournal

JOB_FIELDS = frozenset({"schema_version", "nonce", "created_at", "program_dir", "database", "credential_file",
                        "journal_dir", "parent_pid", "source_version", "release_id", "port"})


def load_job(path: Path, *, protector=None) -> dict:
    path = regular(path)
    try:
        if not path.is_file() or path.stat().st_size > 32768:
            raise ValueError
        value = json.loads((protector or WindowsDpapiProtector()).unprotect(path.read_bytes()))
        if (not isinstance(value, dict) or set(value) != JOB_FIELDS or value["schema_version"] != 1
            or not isinstance(value["nonce"], str) or len(value["nonce"]) != 64
            or any(c not in "0123456789abcdef" for c in value["nonce"])
            or type(value["created_at"]) is not int or not -5 <= time.time() - value["created_at"] <= 3600
            or type(value["parent_pid"]) is not int or value["parent_pid"] <= 0
            or type(value["port"]) is not int or not 1024 <= value["port"] <= 65535):
            raise ValueError
        version_parts(value["source_version"])
        for field in ("program_dir", "database", "credential_file", "journal_dir"):
            if not isinstance(value[field], str) or not Path(value[field]).is_absolute():
                raise ValueError
            regular(Path(value[field]))
        if path.parent != Path(value["journal_dir"]) or path.name != f"job-{value['nonce']}.dpapi":
            raise ValueError
        return value
    except Exception:
        raise UpdateValidationError("A autorização local da atualização expirou ou é inválida.") from None


def launch_update_helper(manager, *, program: Path, database: Path, credential_file: Path, port: int) -> Path:
    _, manifest = manager.ready_artifact()
    ensure_no_pending_restore(database)
    program = regular(program)
    helper = program / "NexPointUpdater.exe"
    if not helper.is_file():
        raise UpdateValidationError("Esta instalação precisa receber o componente de atualização da NexPoint.")
    regular(helper)
    nonce = secrets.token_hex(32)
    runtime = manager.journal.directory / f"helper-{nonce}.exe"
    shutil.copyfile(helper, runtime)
    if file_digest(helper) != file_digest(runtime):
        raise UpdateValidationError("O componente de atualização não pôde ser preparado.")
    job = {"schema_version": 1, "nonce": nonce, "created_at": int(time.time()), "program_dir": str(program),
           "database": str(regular(database)), "credential_file": str(regular(credential_file)),
           "journal_dir": str(manager.journal.directory), "parent_pid": os.getpid(),
           "source_version": manager.installed_version, "release_id": manifest.release_id, "port": port}
    path = manager.journal.directory / f"job-{nonce}.dpapi"
    protected = WindowsDpapiProtector().protect(json.dumps(job, separators=(",", ":")).encode())
    with path.open("xb") as stream:
        stream.write(protected)
        stream.flush()
        os.fsync(stream.fileno())
    manager.stop()
    try:
        subprocess.Popen([str(runtime), "--job", str(path)], cwd=str(manager.journal.directory),
                         creationflags=subprocess.CREATE_NO_WINDOW, close_fds=True)
    except OSError:
        path.unlink(missing_ok=True)
        manager.start()
        raise UpdateValidationError("Não foi possível iniciar o atualizador. O ERP permanece aberto.") from None
    return path


def ensure_no_pending_restore(database: Path) -> None:
    pending = regular(database.parent / "backups" / "restore" / "pending.json")
    if pending.exists():
        raise UpdateValidationError("Conclua ou cancele a restauração pendente antes de atualizar o ERP.")


def validate_desktop_startup(settings, program: Path, *, health_job: Path | None = None, claim_health=False):
    """Run before create_app: it can restore data and apply schema migrations."""
    from sqlalchemy.engine import make_url
    database = regular(Path(make_url(settings.database_url).database))
    credential_path = regular(Path(settings.installation_credentials_path))
    credentials = InstallationCredentialStore(credential_path).load()
    journal = UpdateJournal(Path(settings.data_directory).parent / "updates", credentials)
    state = journal.load()  # An unreadable journal cannot prove recovery is safe.
    if health_job is None:
        if state["state"] in {"INSTALLING", "RESTARTING", "ROLLBACK_REQUIRED"}:
            raise UpdateValidationError("A atualização anterior precisa de recuperação. Não apague dados; contate a NexPoint.")
        return journal, credentials
    job = load_job(health_job)
    if (state["state"] != "RESTARTING" or regular(program) != Path(job["program_dir"])
        or database != Path(job["database"]) or credential_path != Path(job["credential_file"])
        or journal.directory != Path(job["journal_dir"]) or settings.port != job["port"]):
        raise UpdateValidationError("A verificação não pertence a esta instalação.")
    manifest = ReleaseManifest.parse(state["release"])
    if (manifest.release_id != job["release_id"] or settings.version != manifest.version
        or settings.commit != manifest.build_commit):
        raise UpdateValidationError("A build iniciada difere da versão autorizada.")
    ensure_no_pending_restore(database)
    if claim_health:
        try:
            with health_job.with_suffix(".checking").open("xb"):
                pass
        except OSError:
            raise UpdateValidationError("A verificação desta atualização já foi iniciada.") from None
    return journal, credentials


def wait_for_parent(pid: int) -> bool:
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE only
    if not handle:
        return ctypes.get_last_error() == 87  # PID no longer exists; access denied fails closed.
    try:
        return kernel.WaitForSingleObject(handle, 60000) == 0
    finally:
        kernel.CloseHandle(handle)


def install_windows(artifact: Path, program: Path) -> None:
    # Inno Setup must neither force-close other programs nor reboot Windows.
    result = subprocess.run([str(artifact), "/SILENT", "/SP-", "/NORESTART", "/NOCLOSEAPPLICATIONS",
                             "/NORESTARTAPPLICATIONS", "/SUPPRESSMSGBOXES", f"/DIR={program}"],
                            cwd=str(artifact.parent), timeout=600, close_fds=True)
    if result.returncode != 0:
        raise UpdateValidationError("A instalação não foi concluída pelo Windows.")


def _health_signature(credentials, nonce: str, payload: dict) -> str:
    data = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hmac.new(credentials.secret.encode(), b"nexpoint-update-health-v1\n" + nonce.encode() + b"\n" + data, sha256).hexdigest()


def verify_new_process(executable: Path, manifest: ReleaseManifest, *, job_path: Path, job: dict, credentials) -> bool:
    report_path = job_path.with_suffix(".health.json")
    if report_path.exists():
        return False  # do not accept an old response
    try:
        result = subprocess.run([str(executable), "--update-health-check", str(job_path)],
                                cwd=str(executable.parent), timeout=120, close_fds=True,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode != 0 or not report_path.is_file() or report_path.stat().st_size > 8192:
            return False
        report = json.loads(report_path.read_bytes())
        payload = report["payload"]
        expected = {"ok": True, "version": manifest.version, "commit": manifest.build_commit,
                    "installation_id": credentials.installation_id, "tenant_id": credentials.tenant_id,
                    "database": True, "credential": True, "operational_access": True}
        return payload == expected and hmac.compare_digest(report["signature"], _health_signature(credentials, job["nonce"], payload))
    except Exception:
        return False


def run_update_job(path: Path) -> str:
    if os.name != "nt":
        raise UpdateValidationError("A instalação automática está disponível somente no Windows.")
    job = load_job(path)
    credentials = InstallationCredentialStore(Path(job["credential_file"])).load()
    journal = UpdateJournal(Path(job["journal_dir"]), credentials)
    state = journal.load()
    manifest = ReleaseManifest.parse(state["release"])
    if manifest.release_id != job["release_id"] or not manifest.check_for_installation(
        installed_version=job["source_version"], channel=state["update_channel"],
        highest_sequence=state["highest_sequences"][state["update_channel"]],
    ):
        raise UpdateValidationError("A versão autorizada diverge da instalação.")
    transaction = UpdateTransaction(journal=journal, program_dir=Path(job["program_dir"]),
        database=Path(job["database"]), credential_file=Path(job["credential_file"]),
        wait_for_exit=lambda _: True, install=install_windows,
        health_check=lambda exe, release: verify_new_process(exe, release, job_path=path, job=job, credentials=credentials),
        restart=lambda exe: restarts.append(exe))
    # Create-once marker rejects two helpers using the same authorized job.
    marker = path.with_suffix(".started")
    with marker.open("xb"):
        pass
    if not wait_for_parent(job["parent_pid"]):
        return "APP_RUNNING"
    from app.updates.process_lock import installation_process_lock
    restarts = []
    with installation_process_lock(Path(job["program_dir"])):
        ensure_no_pending_restore(Path(job["database"]))
        result = transaction.execute(job["parent_pid"])
    for executable in restarts:
        try:
            process = subprocess.Popen([str(executable)], cwd=str(executable.parent), close_fds=True)
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                continue
            raise OSError("restart exited before acceptance")
        except OSError:
            journal.save(journal.load() | {"state": "FAILED", "error_code": "RESTART_FAILED"})
            return "RESTART_FAILED"
    return result


def write_health_result(path: Path, application, settings) -> None:
    job = load_job(path)
    journal, credentials = validate_desktop_startup(settings, Path(job["program_dir"]), health_job=path)
    if Path(application.state.database_path).resolve() != Path(job["database"]).resolve():
        raise UpdateValidationError("O banco iniciado não pertence à instalação esperada.")
    if (application.state.control_center_tenant_id != credentials.tenant_id
        or application.state.control_center_installation_id != credentials.installation_id):
        raise UpdateValidationError("A identidade iniciada diverge da instalação esperada.")
    schema_snapshot(Path(job["database"]))
    manifest = ReleaseManifest.parse(journal.load()["release"])
    if (manifest.release_id != job["release_id"] or settings.version != manifest.version
            or settings.commit != manifest.build_commit):
        raise UpdateValidationError("A build iniciada difere da versão autorizada.")
    # run_desktop starts the loopback server before this call. Check both routes
    # with redirects disabled and no login credentials or external network.
    from urllib.request import build_opener, HTTPRedirectHandler, ProxyHandler
    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, *_args, **_kwargs):
            return None
    opener = build_opener(ProxyHandler({}), NoRedirect())
    for route in ("/health", "/clientes/lista"):
        with opener.open(f"http://127.0.0.1:{job['port']}{route}", timeout=5) as response:
            if response.status != 200:
                raise UpdateValidationError("A nova versão não disponibilizou o login.")
    payload = {"ok": True, "version": settings.version, "commit": settings.commit,
               "installation_id": credentials.installation_id, "tenant_id": credentials.tenant_id,
               "database": True, "credential": True, "operational_access": True}
    target = path.with_suffix(".health.json")
    with target.open("x", encoding="utf-8") as stream:
        json.dump({"payload": payload, "signature": _health_signature(credentials, job["nonce"], payload)}, stream)
        stream.flush()
        os.fsync(stream.fileno())
