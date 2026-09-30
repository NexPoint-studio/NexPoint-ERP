"""SD-008: deterministic scrypt work oracle plus generous relative timing check."""
import secrets
import statistics
import time

import pytest

from control_center.domain import PlatformUser
from control_center.local_repository import LocalControlCenterRepository
import control_center.security as security


@pytest.fixture
def repository(tmp_path):
    repo = LocalControlCenterRepository(tmp_path / 'login-qa.sqlite3')
    password = secrets.token_urlsafe(24)
    user = repo.save_platform_user(PlatformUser(id='qa', username='qa-admin', display_name='QA'), password=password)
    repo.save_platform_user(PlatformUser(id='inactive', username='qa-inactive', display_name='QA', active=False), password=password)
    yield repo, password, user
    repo.close()


def test_failed_logins_perform_identical_scrypt_parameters(repository, monkeypatch):
    repo, password, user = repository
    calls = []
    original = security.hashlib.scrypt
    def record(value, **kwargs):
        calls.append((kwargs['n'], kwargs['r'], kwargs['p'], kwargs['dklen'], len(kwargs['salt'])))
        return original(value, **kwargs)
    monkeypatch.setattr(security.hashlib, 'scrypt', record)
    for username in ('qa-admin', 'qa-missing', 'qa-inactive'):
        before = len(calls)
        assert repo.authenticate_platform_user(username, 'TEST_ONLY_wrong_password') is None
        assert len(calls) == before + 1
    assert len(set(calls)) == 1
    assert repo.authenticate_platform_user('qa-admin', password).id == user.id
    assert repo.authenticate_platform_user('qa-missing', password) is None
    assert repo.authenticate_platform_user('qa-inactive', password) is None


def test_login_failure_timing_has_no_fast_missing_user_path(repository, record_property):
    repo, _, _ = repository
    samples = {'existing': [], 'missing': []}
    for _ in range(2):
        for name in ('qa-admin', 'qa-missing'):
            repo.authenticate_platform_user(name, 'TEST_ONLY_wrong_password')
    for index in range(12):
        pairs = [('existing', 'qa-admin'), ('missing', 'qa-missing')]
        if index % 2:
            pairs.reverse()
        for label, name in pairs:
            start = time.perf_counter()
            assert repo.authenticate_platform_user(name, 'TEST_ONLY_wrong_password') is None
            samples[label].append(time.perf_counter() - start)
    medians = {key: statistics.median(value) for key, value in samples.items()}
    ratio = medians['missing'] / medians['existing']
    record_property('existing_median_ms', round(medians['existing'] * 1000, 3))
    record_property('missing_median_ms', round(medians['missing'] * 1000, 3))
    record_property('missing_existing_ratio', round(ratio, 3))
    # Old early-return differs by orders of magnitude. This wide relative band
    # tolerates scheduling noise; exact crypto-call assertions carry the contract.
    assert 0.2 <= ratio <= 5
