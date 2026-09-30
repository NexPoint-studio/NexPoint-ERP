"""SD-003 release prevention; names only, no real profile is opened."""
import subprocess
from pathlib import Path

import pytest

from scripts.secret_scan import blocked_path


BLOCKED = [
    'carca\u00e7a/Default/History', 'carca\u00e7a/Default/Preferences',
    'carca\u00e7a/SmartScreen/Local State', 'browser-profile/opaque',
    'cache/User Data/Default/Preferences', 'cache/Profile 1/Bookmarks',
    'cache/EBWebView/Default/History', 'cache/Session Storage/000001.ldb',
    'cache/Local Storage/leveldb/000001.ldb', 'cache/Sync Data/LevelDB/000001.ldb',
    'cache/Network/Cookies', 'cache/Login Data', 'cache/Login Data For Account',
    'cache/Web Data', 'cache/History-journal', 'cache/Local State',
    'cache/Cookies-journal', 'cache/Sessions/Session_123',
]


@pytest.mark.parametrize('path', BLOCKED)
def test_release_guard_rejects_browser_artifact(path):
    assert blocked_path(path) == 'browser profile artifact'


@pytest.mark.parametrize('path', ['app/core/session.py', 'Docs/History.md',
                                  'control_center/static/control.js', 'tests/test_sessions.py'])
def test_official_source_names_are_not_blocked(path):
    assert blocked_path(path) is None


def test_gitignore_blocks_browser_paths_including_arbitrary_profile_files(tmp_path):
    # Standalone repo so the isolated export need not inherit the user's .git.
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    root = Path(__file__).resolve().parents[1]
    (tmp_path / '.gitignore').write_bytes((root / '.gitignore').read_bytes())
    result = subprocess.run(['git', 'check-ignore', '--stdin', '-z'], cwd=tmp_path,
                            input=('\0'.join(BLOCKED)+'\0').encode('utf-8'),
                            capture_output=True, check=False)
    assert result.returncode == 0
    assert set(result.stdout.decode('utf-8').split('\0')) - {''} == set(BLOCKED)
