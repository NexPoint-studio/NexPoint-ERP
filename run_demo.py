from __future__ import annotations

import argparse
from contextlib import closing, suppress
import ctypes
from ctypes import wintypes
from dataclasses import dataclass, replace
from hashlib import sha256
import os
from pathlib import Path
import secrets
import sqlite3
import sys

from fastapi import Request
from fastapi.responses import PlainTextResponse

from app import create_app
from app.core.config import ROOT_DIR
from app.services.system_maintenance import (
    DatabaseSnapshot,
    MaintenanceValidationError,
    validate_database,
)
import run_desktop as desktop_runtime


DEMO_HOST = "127.0.0.1"
DEMO_PORT = 8766
DEMO_DATA_DIRECTORY = ROOT_DIR / "data"
DEMO_DATABASE_PATH = DEMO_DATA_DIRECTORY / "demo_2_anos.sqlite3"
OPERATIONAL_DATABASE_PATH = DEMO_DATA_DIRECTORY / "erp.sqlite3"
DEMO_OWNER_LOGIN = "proprietario@demo.local"
DEMO_PROFILE_KEY = "demo.profile"
DEMO_PROFILE_VALUE = "nexpoint-2-years-20260909"
DEMO_CONTROL_CENTER_IDENTITY = sha256(
    f"nexpoint-demo-control-center:{DEMO_PROFILE_VALUE}".encode("ascii")
).hexdigest()
DEMO_WINDOW_TITLE = "ERP — DEMONSTRAÇÃO (2 anos)"
DEMO_RESTORE_ROUTE = "/admin/sistema/restauracao"
DEMO_RESTORE_ROUTES = {
    DEMO_RESTORE_ROUTE,
    "/admin/sistema/restauracao/cancelar",
}

_FILE_READ_ATTRIBUTES = 0x0080
_FILE_SHARE_READ = 0x00000001
_FILE_SHARE_WRITE = 0x00000002
_OPEN_EXISTING = 3
_FILE_ATTRIBUTE_DIRECTORY = 0x00000010
_FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
_INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value


class _ByHandleFileInformation(ctypes.Structure):
    _fields_ = [
        ("file_attributes", wintypes.DWORD),
        ("creation_time", wintypes.FILETIME),
        ("last_access_time", wintypes.FILETIME),
        ("last_write_time", wintypes.FILETIME),
        ("volume_serial_number", wintypes.DWORD),
        ("file_size_high", wintypes.DWORD),
        ("file_size_low", wintypes.DWORD),
        ("number_of_links", wintypes.DWORD),
        ("file_index_high", wintypes.DWORD),
        ("file_index_low", wintypes.DWORD),
    ]


class DemoLaunchError(RuntimeError):
    """Falha fechada antes de abrir ou modificar qualquer banco."""


@dataclass(frozen=True, slots=True)
class ValidatedDemoDatabase:
    path: Path
    snapshot: DatabaseSnapshot


@dataclass(frozen=True, slots=True)
class _WindowsFileIdentity:
    volume_serial_number: int
    file_index: int


@dataclass(frozen=True, slots=True)
class _LockedWindowsPath:
    path: Path
    handle: int
    identity: _WindowsFileIdentity
    is_directory: bool


def _lexical_path(path: Path) -> Path:
    """Normaliza sem resolver junctions ou links simbólicos."""

    return Path(os.path.abspath(os.fspath(path)))


def _path_key(path: Path) -> str:
    return os.path.normcase(os.fspath(_lexical_path(path)))


def _windows_api():
    if os.name != "nt":
        raise DemoLaunchError(
            "O bloqueio forte do banco demo requer o runtime Windows deste projeto."
        )
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    api.CreateFileW.restype = wintypes.HANDLE
    api.GetFileInformationByHandle.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_ByHandleFileInformation),
    ]
    api.GetFileInformationByHandle.restype = wintypes.BOOL
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    api.CloseHandle.restype = wintypes.BOOL
    return api


def _close_windows_handle(handle: int) -> None:
    if os.name == "nt" and handle != _INVALID_HANDLE_VALUE:
        _windows_api().CloseHandle(handle)


def _open_locked_windows_path(path: Path, *, is_directory: bool) -> _LockedWindowsPath:
    api = _windows_api()
    flags = _FILE_FLAG_OPEN_REPARSE_POINT
    if is_directory:
        flags |= _FILE_FLAG_BACKUP_SEMANTICS
    handle = api.CreateFileW(
        os.fspath(_lexical_path(path)),
        _FILE_READ_ATTRIBUTES,
        _FILE_SHARE_READ | _FILE_SHARE_WRITE,
        None,
        _OPEN_EXISTING,
        flags,
        None,
    )
    if handle == _INVALID_HANDLE_VALUE:
        code = ctypes.get_last_error()
        raise DemoLaunchError(
            f"Não foi possível bloquear {path} com segurança (erro Win32 {code})."
        )
    information = _ByHandleFileInformation()
    if not api.GetFileInformationByHandle(handle, ctypes.byref(information)):
        code = ctypes.get_last_error()
        _close_windows_handle(handle)
        raise DemoLaunchError(
            f"Não foi possível identificar {path} com segurança (erro Win32 {code})."
        )
    attributes = int(information.file_attributes)
    actual_is_directory = bool(attributes & _FILE_ATTRIBUTE_DIRECTORY)
    if actual_is_directory != is_directory:
        _close_windows_handle(handle)
        raise DemoLaunchError("O caminho protegido do modo demo possui tipo inesperado.")
    if attributes & _FILE_ATTRIBUTE_REPARSE_POINT:
        _close_windows_handle(handle)
        raise DemoLaunchError(
            "O modo demo recusa links simbólicos, junctions e outros reparse points."
        )
    identity = _WindowsFileIdentity(
        volume_serial_number=int(information.volume_serial_number),
        file_index=(int(information.file_index_high) << 32) | int(information.file_index_low),
    )
    return _LockedWindowsPath(_lexical_path(path), handle, identity, is_directory)


class DemoDatabaseGuard:
    """Mantém caminho e SQLite imutáveis por nome durante toda a execução."""

    def __init__(
        self,
        database_path: Path | None = None,
        *,
        operational_database_path: Path | None = None,
        expected_database_path: Path | None = None,
        data_directory: Path | None = None,
    ) -> None:
        self.database_path = _lexical_path(database_path or DEMO_DATABASE_PATH)
        self.operational_database_path = _lexical_path(
            operational_database_path or OPERATIONAL_DATABASE_PATH
        )
        self.expected_database_path = _lexical_path(
            expected_database_path or DEMO_DATABASE_PATH
        )
        self.data_directory = _lexical_path(
            data_directory or self.expected_database_path.parent
        )
        self._locked: list[_LockedWindowsPath] = []
        self.active = False

    def acquire(self) -> DemoDatabaseGuard:
        if self.active:
            raise DemoLaunchError("O bloqueio do banco demo já está ativo.")
        if _path_key(self.database_path) != _path_key(self.expected_database_path):
            raise DemoLaunchError("O bloqueio recebeu um caminho diferente do banco demo oficial.")
        if _path_key(self.expected_database_path.parent) != _path_key(self.data_directory):
            raise DemoLaunchError("O banco demo não está no diretório local configurado.")
        if not self.database_path.exists():
            raise DemoLaunchError(
                "O banco demo ainda não existe. Execute primeiro o gerador oficial com confirmação demo."
            )
        try:
            for path, is_directory in (
                (self.data_directory.parent, True),
                (self.data_directory, True),
                (self.database_path, False),
            ):
                self._locked.append(
                    _open_locked_windows_path(path, is_directory=is_directory)
                )
            self.active = True
            self.verify()
        except BaseException:
            self.close()
            raise
        return self

    def verify(self) -> None:
        if not self.active or len(self._locked) != 3:
            raise DemoLaunchError("O banco demo não está protegido contra troca de arquivo.")
        for locked in self._locked:
            current = _open_locked_windows_path(
                locked.path,
                is_directory=locked.is_directory,
            )
            try:
                if current.identity != locked.identity:
                    raise DemoLaunchError("A identidade do caminho demo mudou durante a execução.")
            finally:
                _close_windows_handle(current.handle)
        if _same_file(self.database_path, self.operational_database_path):
            raise DemoLaunchError("O modo demo nunca pode bloquear o banco operacional.")

    def validate(self) -> ValidatedDemoDatabase:
        self.verify()
        validated = validate_demo_database(
            self.database_path,
            operational_database_path=self.operational_database_path,
            expected_database_path=self.expected_database_path,
        )
        self.assert_protects(validated.path)
        return validated

    def assert_protects(self, path: Path) -> None:
        self.verify()
        if _path_key(path) != _path_key(self.database_path):
            raise DemoLaunchError("O bloqueio ativo não corresponde ao banco demo solicitado.")

    def close(self) -> None:
        locked, self._locked = self._locked, []
        self.active = False
        for item in reversed(locked):
            with suppress(Exception):
                _close_windows_handle(item.handle)

    def __enter__(self) -> DemoDatabaseGuard:
        return self.acquire()

    def __exit__(self, _type, _value, _traceback) -> None:
        self.close()

    def __del__(self) -> None:
        self.close()


def _same_file(first: Path, second: Path) -> bool:
    """Compara também hardlinks; falha fechada se o SO não puder confirmar."""

    if first.resolve(strict=False) == second.resolve(strict=False):
        return True
    if not first.exists() or not second.exists():
        return False
    try:
        return os.path.samefile(first, second)
    except OSError as error:
        raise DemoLaunchError(
            "Não foi possível confirmar o isolamento entre os bancos demo e operacional."
        ) from error


def validate_demo_database(
    database_path: Path | None = None,
    *,
    operational_database_path: Path | None = None,
    expected_database_path: Path | None = None,
) -> ValidatedDemoDatabase:
    """Valida o SQLite demo existente sem criá-lo nem aceitar outro caminho."""

    candidate = Path(database_path or DEMO_DATABASE_PATH)
    operational = Path(operational_database_path or OPERATIONAL_DATABASE_PATH)
    expected = Path(expected_database_path or DEMO_DATABASE_PATH)

    if _same_file(candidate, operational):
        raise DemoLaunchError("O modo demo nunca pode abrir o banco operacional.")
    if candidate.resolve(strict=False) != expected.resolve(strict=False):
        raise DemoLaunchError(
            f"O modo demo aceita somente o banco configurado em {expected.resolve(strict=False)}."
        )
    if not candidate.exists():
        raise DemoLaunchError(
            "O banco demo ainda não existe. Execute primeiro o gerador oficial com confirmação demo."
        )

    try:
        snapshot = validate_database(candidate, require_latest=True)
    except MaintenanceValidationError as error:
        raise DemoLaunchError(f"O banco demo foi recusado: {error}") from error
    try:
        uri = candidate.resolve(strict=True).as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=5)) as connection:
            connection.execute("pragma query_only=on")
            marker = connection.execute(
                "select value from settings where key = ?",
                (DEMO_PROFILE_KEY,),
            ).fetchone()
            owner = connection.execute(
                "select 1 from users u "
                "join user_roles ur on ur.user_id = u.id "
                "join roles r on r.id = ur.role_id "
                "where u.email = ? and u.active = 1 and r.code = 'admin' limit 1",
                (DEMO_OWNER_LOGIN,),
            ).fetchone()
    except (OSError, sqlite3.DatabaseError) as error:
        raise DemoLaunchError("Não foi possível confirmar a identidade do banco demo.") from error
    if marker != (DEMO_PROFILE_VALUE,):
        raise DemoLaunchError("O arquivo não possui o marcador exclusivo do ambiente demo.")
    if owner is None:
        raise DemoLaunchError("O banco demo não possui o Proprietário fictício esperado.")
    return ValidatedDemoDatabase(path=candidate.resolve(strict=True), snapshot=snapshot)


def _create_demo_application(
    validated: ValidatedDemoDatabase,
    *,
    guard: DemoDatabaseGuard,
):
    guard.assert_protects(validated.path)
    pending_restore = validated.path.parent / "backups" / "restore" / "pending.json"
    if pending_restore.exists():
        raise DemoLaunchError(
            "O modo demo recusou uma restauração pendente para preservar o dataset original."
        )
    guard.assert_protects(validated.path)
    database_url = f"sqlite+pysqlite:///{validated.path.as_posix()}"
    application = create_app(
        database_url=database_url,
        credentials={},
        # Cada abertura invalida cookies demo antigos e nunca reutiliza o
        # segredo nem as credenciais do ambiente operacional.
        session_secret=secrets.token_urlsafe(48),
        session_cookie="erp_demo_session",
        restore_enabled=False,
        shutdown_callback=guard.close,
        control_center_identity=DEMO_CONTROL_CENTER_IDENTITY,
    )
    guard.assert_protects(validated.path)
    active_path = Path(application.state.database_path).resolve(strict=False)
    if active_path != validated.path or _same_file(active_path, OPERATIONAL_DATABASE_PATH):
        application.state.engine.dispose()
        raise DemoLaunchError("A aplicação não confirmou o banco demo isolado.")
    try:
        # create_app aplica uma restauração previamente preparada antes de criar
        # o engine. A identidade é conferida de novo para impedir que esse fluxo
        # substitua silenciosamente o ambiente demo por outro conteúdo.
        confirmed = validate_demo_database(
            active_path,
            operational_database_path=OPERATIONAL_DATABASE_PATH,
            expected_database_path=validated.path,
        )
    except BaseException:
        application.state.engine.dispose()
        raise

    application.state.demo_mode = True
    application.state.demo_owner_login = DEMO_OWNER_LOGIN
    application.state.demo_database_path = confirmed.path
    application.state.demo_database_guard = guard
    application.state.demo_restore_disabled = True
    application.state.settings = replace(
        application.state.settings,
        app_name=DEMO_WINDOW_TITLE,
        build="demo-2-anos",
        environment="demo",
        host=DEMO_HOST,
        port=DEMO_PORT,
    )

    @application.middleware("http")
    async def preserve_original_demo_database(request: Request, call_next):
        if request.method == "POST" and request.url.path in DEMO_RESTORE_ROUTES:
            return PlainTextResponse(
                "A restauração direta é desativada no modo demo. Valide restaurações em uma cópia isolada.",
                status_code=403,
            )
        return await call_next(request)

    return application


def build_demo_application():
    """Cria a aplicação e mantém o guard em ``app.state`` até o descarte."""

    guard = DemoDatabaseGuard()
    try:
        guard.acquire()
        application = _create_demo_application(guard.validate(), guard=guard)
    except BaseException:
        guard.close()
        raise
    return application


def run_demo() -> None:
    with DemoDatabaseGuard() as guard:
        validated = guard.validate()
        # A porta é verificada antes do bootstrap, para uma instância concorrente
        # não provocar sequer uma escrita incidental no SQLite demo.
        desktop_runtime.ensure_port_available(DEMO_HOST, DEMO_PORT)
        application = _create_demo_application(validated, guard=guard)
        server, thread = desktop_runtime.start_local_server(application, DEMO_HOST, DEMO_PORT)
        try:
            desktop_runtime.webview.create_window(
                DEMO_WINDOW_TITLE,
                f"http://{DEMO_HOST}:{DEMO_PORT}",
                width=1440,
                height=900,
                min_size=(960, 640),
                background_color="#f5f5f5",
            )
            desktop_runtime.webview.start(debug=False, private_mode=True)
        finally:
            desktop_runtime.stop_local_server(server, thread)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Abre exclusivamente o banco persistente da demonstração de 2 anos."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="valida o banco demo sem iniciar servidor ou janela",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    if arguments.check:
        with DemoDatabaseGuard() as guard:
            validated = guard.validate()
        print(f"Banco demo válido: {validated.path}")
        print(
            f"Schema: {validated.snapshot.schema_version} | "
            f"SHA-256: {validated.snapshot.sha256}"
        )
        print(f"Login demo: {DEMO_OWNER_LOGIN}")
        return 0
    run_demo()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as error:
        if sys.platform == "win32":
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, str(error), DEMO_WINDOW_TITLE, 0x10)
        else:
            print(str(error), file=sys.stderr)
        raise SystemExit(1) from None
