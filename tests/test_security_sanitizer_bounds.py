"""SD-002 regression with synthetic data and bounded work, not precise timings."""
import json
import subprocess
import sys

import pytest

from control_center.sanitization import (
    REDACTED, contains_secret_material, sanitize_payload, sanitize_text,
)


@pytest.mark.parametrize('value', [
    'ordinary text ' * 1000,
    'x' * 8000 + ' password=TEST_ONLY_not_a_real_password',
    '\U0001f512' * 8001,
    '-----BEGIN PRIVATE KEY-----' * 8192,  # TEST_ONLY incomplete delimiters; no key
], ids=['ordinary', 'sensitive-suffix', 'unicode', 'private-key-markers'])
def test_oversized_input_is_redacted_before_matching(value):
    assert sanitize_text(value) == REDACTED
    assert contains_secret_material(value)  # Cannot safely certify an oversized value.


def test_incomplete_private_key_never_leaks_body():
    value = '-----BEGIN RSA PRIVATE KEY-----\nTEST_ONLY_private_body'
    assert sanitize_text(value) == REDACTED
    assert contains_secret_material(value)


def test_normal_unicode_and_sensitive_fields_remain_supported():
    assert sanitize_text('  operacao \u00e7\u00e3o \U0001f512  ') == 'operacao \u00e7\u00e3o \U0001f512'
    data = sanitize_payload({'status': 'ok', 'password': 'TEST_ONLY_value',
                             'details': 'email=test@example.invalid',
                             'nested': {'inner': ['ordinary', {'token': 'TEST_ONLY_value'}]}})
    assert data['status'] == 'ok' and data['password'] == REDACTED
    assert 'test@example.invalid' not in json.dumps(data)
    assert data['nested']['inner'][1]['token'] == REDACTED


def test_long_sensitive_key_is_checked_before_display_truncation():
    result = sanitize_payload({'x' * 130 + '_password': 'TEST_ONLY_private_value'})
    assert 'TEST_ONLY_private_value' not in json.dumps(result)


def test_collection_is_not_fully_copied_before_limiting():
    class BoundedList(list):
        def __iter__(self):
            for _ in range(100):
                yield 'ok'
            raise AssertionError('sanitizer consumed beyond its collection limit')
    assert sanitize_payload(BoundedList()) == ['ok'] * 100


def test_shared_nested_payload_has_a_global_work_budget():
    value = [['normal' for _ in range(100)] for _ in range(100)]
    result = sanitize_payload(value)
    assert json.dumps(result).count('normal') <= 512
    large_strings = [['x' * 7999] * 100] * 100
    assert len(json.dumps(sanitize_payload(large_strings))) < 100_000
    deep = 'TEST_ONLY_secret'
    for _ in range(30):
        deep = [deep]
    assert 'TEST_ONLY_secret' not in json.dumps(sanitize_payload(deep))


def test_original_adversarial_pattern_finishes_with_generous_timeout():
    # TEST_ONLY incomplete delimiters, not a key.
    code = "from control_center.sanitization import sanitize_text; sanitize_text('-----BEGIN PRIVATE KEY-----'*8192)"
    # The old regex exceeded five seconds. Ten seconds allows slow QA hosts;
    # no millisecond performance assertion or secret value is printed.
    result = subprocess.run([sys.executable, '-c', code], capture_output=True, timeout=10)
    assert result.returncode == 0
