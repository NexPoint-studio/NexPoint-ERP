"""SD-002: the local telemetry boundary must limit work before matching."""
import json

import pytest

from app.observability.events import build_observability_event
from app.observability.sanitization import (
    REDACTED, SECRET_CANARY, contains_secret_material, sanitize_metadata,
    sanitize_text, sanitize_token,
)


@pytest.mark.parametrize("value", [
    "ordinary " * 1000,
    "x" * 8000 + " password=TEST_ONLY_not_a_real_password",
    "\U0001f512" * 8001,
    "-----BEGIN PRIVATE KEY-----" * 512,  # TEST_ONLY incomplete delimiters.
], ids=["ordinary", "sensitive-suffix", "unicode", "private-key-markers"])
def test_oversized_observability_text_is_redacted_before_matching(value):
    assert sanitize_text(value) == REDACTED
    assert contains_secret_material(value)


def test_incomplete_observability_private_key_is_fully_redacted():
    value = "-----BEGIN RSA PRIVATE KEY-----\nTEST_ONLY_private_body"
    assert sanitize_text(value) == REDACTED
    assert contains_secret_material(value)


def test_token_length_is_checked_before_secret_matching(monkeypatch):
    import app.observability.sanitization as sanitization

    def forbidden(_value):
        raise AssertionError("oversized token reached secret matching")

    monkeypatch.setattr(sanitization, "contains_secret_material", forbidden)
    assert sanitize_token("x" * 8001, "unknown") == "unknown"


def test_metadata_collection_is_not_fully_copied_or_scanned():
    class BoundedList(list):
        def __iter__(self):
            yield from ("ok" for _ in range(50))
            raise AssertionError("collection inspected beyond its limit")

    result = dict(sanitize_metadata({"reason": BoundedList()}))
    assert result["reason"] in (["ok"] * 50, REDACTED)


def test_metadata_has_a_shared_work_budget():
    nested = [["ordinary"] * 50 for _ in range(50)]
    result = dict(sanitize_metadata({"reason": nested, "source": nested}))
    assert json.dumps(result).count("ordinary") <= 512
    long_text = [["x" * 7999] * 50] * 50
    assert len(json.dumps(dict(sanitize_metadata({"reason": long_text})))) < 100_000


def test_metadata_depth_is_bounded_without_revealing_hidden_value():
    value = "TEST_ONLY_deep_value"
    for _ in range(40):
        value = [value]
    rendered = json.dumps(dict(sanitize_metadata({"reason": value})))
    assert "TEST_ONLY_deep_value" not in rendered
    assert len(rendered) < 100


def test_metadata_ignores_untrusted_keys_without_iterating_them():
    class DirectLookupMapping(dict):
        def __iter__(self):
            raise AssertionError("untrusted metadata key collection was iterated")

    result = dict(sanitize_metadata(DirectLookupMapping(reason="ordinary", ignored="x")))
    assert result == {"reason": "ordinary"}


def test_normal_metadata_and_nested_canary_contract_are_preserved():
    normal = dict(sanitize_metadata({
        "reason": ["opera\u00e7\u00e3o \U0001f512", ["ordinary"]],
        "http_status": 422, "retryable": True,
        "password": "TEST_ONLY_private", "unapproved": "ignored",
    }))
    assert normal == {"http_status": 422, "reason": ["opera\u00e7\u00e3o \U0001f512", ["ordinary"]],
                      "retryable": True}
    protected = dict(sanitize_metadata({"reason": ["ordinary", [SECRET_CANARY]]}))
    assert protected == {"reason": REDACTED}
    assert "test@example.invalid" not in sanitize_text("email=test@example.invalid")


def test_http_route_metadata_is_bounded_at_event_boundary():
    event = build_observability_event(
        level="INFO", environment="test", tenant_id="tenant_test",
        installation_id="installation_test", module="static", component="fastapi",
        event_type="http.request.started", operation="post", status="started",
        metadata={"route": "/static/" + "x" * 8001, "method": "POST"},
    )
    assert event.metadata == {"method": "POST", "route": REDACTED}
