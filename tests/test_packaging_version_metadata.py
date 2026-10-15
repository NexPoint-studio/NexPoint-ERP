import re
import tomllib
from pathlib import Path


def test_windows_resource_version_matches_project_release():
    root = Path(__file__).resolve().parents[1]
    version = tomllib.loads((root / 'pyproject.toml').read_text(encoding='utf-8'))['project']['version']
    resource = (root / 'packaging/windows_version_info.txt').read_text(encoding='utf-8')
    for field in ('FileVersion', 'ProductVersion'):
        assert re.search(r"StringStruct\(u'" + field + r"', u'([^']+)'\)", resource).group(1) == version
    numbers = tuple(map(int, version.split('.'))) + (0,)
    for field in ('filevers', 'prodvers'):
        assert tuple(map(int, re.search(field + r'=\(([^)]+)\)', resource).group(1).split(','))) == numbers
