"""An installation-wide mutex shared by desktop and updater processes."""
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path

from app.updates.contracts import UpdateValidationError


@contextmanager
def installation_process_lock(program_dir: Path):
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateMutexW.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.ReleaseMutex.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    scope = sha256(str(program_dir.resolve()).casefold().encode()).hexdigest()
    handle = kernel.CreateMutexW(None, False, "Local\\NexPointERPUpdate-" + scope)
    if not handle:
        raise UpdateValidationError("Não foi possível proteger a instalação durante a atualização.")
    acquired = False
    try:
        acquired = kernel.WaitForSingleObject(handle, 5000) in {0, 0x80}
        if not acquired:
            raise UpdateValidationError("O ERP ou o atualizador já está aberto. Aguarde sua conclusão.")
        yield
    finally:
        if acquired:
            kernel.ReleaseMutex(handle)
        kernel.CloseHandle(handle)
