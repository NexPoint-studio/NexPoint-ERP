"""Launch the private, local NexPoint ERP Control Center."""

from __future__ import annotations

import sys

from control_center.config import get_control_center_settings
from control_center.web import create_control_center_app
import run_desktop as desktop_runtime


WINDOW_TITLE = "NexPoint ERP Control Center"


def run_control_center() -> None:
    settings = get_control_center_settings()
    desktop_runtime.ensure_port_available(settings.host, settings.port)
    application = create_control_center_app()
    server, thread = desktop_runtime.start_local_server(
        application, settings.host, settings.port
    )
    try:
        desktop_runtime.webview.create_window(
            WINDOW_TITLE,
            f"http://{settings.host}:{settings.port}",
            width=1500,
            height=920,
            min_size=(960, 640),
            background_color="#eef2f5",
        )
        desktop_runtime.webview.start(debug=False, private_mode=True)
    finally:
        desktop_runtime.stop_local_server(server, thread)


if __name__ == "__main__":
    try:
        run_control_center()
    except RuntimeError as error:
        if sys.platform == "win32":
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, str(error), WINDOW_TITLE, 0x10)
        else:
            print(str(error), file=sys.stderr)
        raise SystemExit(1) from None

