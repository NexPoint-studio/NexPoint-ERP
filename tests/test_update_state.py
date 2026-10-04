import json
import pytest

from app.updates.contracts import UpdateValidationError
from app.updates.state import UpdateJournal
from tests.test_update_remote import CREDENTIALS
from tests.test_update_contracts_download import manifest_dict


def available(journal):
    value = journal.load()
    value.update(state="AVAILABLE", update_channel="stable", release=manifest_dict())
    value["highest_sequences"]["stable"] = 2
    return value


def test_journal_persists_without_secret_and_rejects_tampering(tmp_path):
    journal = UpdateJournal(tmp_path, CREDENTIALS)
    state = journal.save(available(journal))
    assert UpdateJournal(tmp_path, CREDENTIALS).load() == state
    encoded = journal.path.read_text(encoding="utf-8")
    assert CREDENTIALS.secret not in encoded and "https://" not in encoded
    altered = json.loads(encoded)
    altered["payload"]["release"]["artifact_sha256"] = "f" * 64
    journal.path.write_text(json.dumps(altered), encoding="utf-8")
    with pytest.raises(UpdateValidationError):
        journal.load()


def test_journal_sequence_is_monotonic_and_same_release_is_immutable(tmp_path):
    journal = UpdateJournal(tmp_path, CREDENTIALS)
    original = journal.save(available(journal))
    changed = journal.load()
    changed["release"]["artifact_sha256"] = "f" * 64
    with pytest.raises(UpdateValidationError):
        journal.save(changed)
    changed = journal.load()
    changed["release"]["sequence"] = changed["highest_sequences"]["stable"] = 1
    with pytest.raises(UpdateValidationError):
        journal.save(changed)
    assert journal.load() == original


def test_journal_binds_installation_and_keeps_channel_sequences_separate(tmp_path):
    from dataclasses import replace
    journal = UpdateJournal(tmp_path, CREDENTIALS)
    journal.save(available(journal))
    with pytest.raises(UpdateValidationError):
        UpdateJournal(tmp_path, replace(CREDENTIALS, installation_id="qa-other-installation")).load()
    state = journal.load()
    state.update(update_channel="internal", release=manifest_dict(update_channel="internal", sequence=1))
    state["highest_sequences"]["internal"] = 1
    journal.save(state)
    assert journal.load()["highest_sequences"] == {"stable": 2, "internal": 1, "beta": 0}


def test_missing_release_or_arbitrary_error_details_cannot_be_persisted(tmp_path):
    journal = UpdateJournal(tmp_path, CREDENTIALS)
    for changes in ({"error_code": "private transport URL"}, {"state": "READY"}, {"url": "https://example.invalid"}):
        with pytest.raises(UpdateValidationError):
            journal.save(journal.load() | changes)
