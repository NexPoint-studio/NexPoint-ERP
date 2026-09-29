"""Run discovery regressions on an exported baseline, never local installation data."""
from pathlib import Path
import os
import socket
import ipaddress
import sys

ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / 'artifacts/security/source'
web_suite = '--web' in sys.argv
if web_suite:
    sys.argv.remove('--web')
OUT = ROOT / ('artifacts/security/webregressions' if web_suite else 'artifacts/security/desktop')
OUT.mkdir(parents=True, exist_ok=True)
safe = {k: v for k, v in os.environ.items() if k.upper() in {
    'SYSTEMROOT', 'WINDIR', 'PATH', 'TEMP', 'TMP', 'COMSPEC', 'PATHEXT',
}}
os.environ.clear()
os.environ.update(safe)
os.environ.update(LOCALAPPDATA=str(OUT / 'appdata'), APPDATA=str(OUT / 'appdata'),
                  PYTEST_DISABLE_PLUGIN_AUTOLOAD='1')
os.chdir(SOURCE)
sys.path.insert(0, str(SOURCE))
sys.argv[0] = str(SOURCE / 'run_desktop.py')
original_connect = socket.socket.connect
original_connect_ex = socket.socket.connect_ex
def guard(original):
    def connect(sock, address):
        if isinstance(address, tuple):
            try:
                allowed = ipaddress.ip_address(address[0]).is_loopback
            except ValueError:
                allowed = address[0] == 'localhost'
            if not allowed:
                raise RuntimeError('External network prohibited in isolated discovery')
        return original(sock, address)
    return connect
socket.socket.connect = guard(original_connect)
socket.socket.connect_ex = guard(original_connect_ex)
import pytest
selected = [
    'tests/test_phase_four_backup_restore.py', 'tests/test_prod_migration_backup.py',
    'tests/test_phase_four_session_audit.py', 'tests/test_final_ux_security.py',
    'tests/test_nexa_bridge.py', 'tests/test_nexa_ui.py',
    'tests/test_stabilization_security.py', 'tests/test_phase_four_admin_security.py',
    'tests/test_prod_sync_client.py::test_windows_dpapi_round_trip_uses_current_user_scope',
] if not web_suite else [
    'tests/test_control_center.py', 'tests/test_control_center_origin.py',
    'tests/test_control_center_hardening.py', 'tests/test_control_center_log_authorization.py',
    'tests/test_prod_control_center.py',
]
raise SystemExit(pytest.main([*selected,
    '-q', '--tb=short', '--basetemp', str(OUT / 'pytest_temp'),
    '--junitxml', str(OUT / 'regressions.xml'), *sys.argv[1:],
]))
