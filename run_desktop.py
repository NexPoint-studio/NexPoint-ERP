from __future__ import annotations

import socket
import threading
import time

import uvicorn
import webview

from app import create_app
from app.core.config import get_settings


def ensure_port_available(host: str, port: int) -> None:
    """Falha antes do bootstrap quando a porta pertence a outro processo."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((host, port))
        except OSError as error:
            raise RuntimeError(
                f"A porta local {host}:{port} já está em uso. "
                "Feche a outra instância do ERP."
            ) from error


def wait_for_server(
    host: str,
    port: int,
    timeout: float = 12,
    *,
    server: uvicorn.Server | None = None,
    thread: threading.Thread | None = None,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if thread is not None and not thread.is_alive():
            raise RuntimeError("O servidor local encerrou antes de iniciar.")
        if server is not None:
            if server.started:
                return
            time.sleep(0.1)
            continue
        try:
            with socket.create_connection((host, port), timeout=0.25):
                return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError("O servidor local não iniciou no prazo esperado.")


def stop_local_server(
    server: uvicorn.Server,
    thread: threading.Thread,
    timeout: float = 5,
) -> None:
    server.should_exit = True
    thread.join(timeout=timeout)
    if thread.is_alive():
        server.force_exit = True
        thread.join(timeout=2)
    if thread.is_alive():
        raise RuntimeError("O servidor local não encerrou corretamente.")


def start_local_server(application, host: str, port: int) -> tuple[uvicorn.Server, threading.Thread]:
    if host != "127.0.0.1":
        raise RuntimeError("O servidor permite somente 127.0.0.1.")
    config = uvicorn.Config(application, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(
        target=server.run,
        name="erp-template-local-api",
        daemon=True,
    )
    thread.start()
    try:
        wait_for_server(host, port, server=server, thread=thread)
    except Exception:
        stop_local_server(server, thread)
        raise
    return server, thread


def run_desktop() -> None:
    settings = get_settings()
    host = "127.0.0.1"
    ensure_port_available(host, settings.port)
    application = create_app()
    server, thread = start_local_server(application, host, settings.port)
    try:
        webview.create_window(
            settings.app_name,
            f"http://127.0.0.1:{settings.port}",
            width=1440,
            height=900,
            min_size=(960, 640),
            background_color="#f5f5f5",
        )
        webview.start(debug=False, private_mode=False)
    finally:
        stop_local_server(server, thread)


if __name__ == "__main__":
    try:
        run_desktop()
    except RuntimeError as error:
        import sys
        if sys.platform == "win32":
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, str(error), "ERP — inicialização", 0x10)
        else:
            print(str(error), file=sys.stderr)
        raise SystemExit(1) from None
