"""Run offline discovery tests against the clean tracked snapshot only."""
import os
from pathlib import Path
import socket
import sys

ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "artifacts/security/source"
OUT = ROOT / "artifacts/security/sync"
OUT.mkdir(parents=True, exist_ok=True)
allowed = {k: v for k, v in os.environ.items() if k.upper() in {
    "SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP", "COMSPEC", "PATHEXT", "NUMBER_OF_PROCESSORS"
}}
os.environ.clear()
os.environ.update(allowed)
os.environ.update(LOCALAPPDATA=str(OUT / "isolated_appdata"), ERP_ENV="test", PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
os.chdir(SOURCE)
sys.path.insert(0, str(SOURCE))
def deny_network(*args, **kwargs):
    raise AssertionError("Security discovery prohibits network access")
socket.create_connection = deny_network
socket.socket.connect = deny_network
socket.socket.connect_ex = deny_network
import pytest
raise SystemExit(pytest.main([
    "tests/test_admin_recovery_remote.py", "tests/test_prod_sync_client.py", "tests/test_offline_sync.py",
    "-k", "not windows_dpapi_round_trip", "-q", "--tb=short",
    "--basetemp", str(OUT / "pytest_temp"), "--junitxml", str(OUT / "python_focused.xml"),
    *sys.argv[1:],
]))
