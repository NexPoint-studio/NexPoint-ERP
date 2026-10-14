import pytest

from app.updates.contracts import ReleaseManifest
from control_center.domain import ControlCenterValidationError, ControlCenterConflictError
from control_center.releases import rollout_eligible
from tests.test_release_catalog import catalog, metadata
from tests.test_control_center import control_app, control_client, control_login, CONTROL_USERNAME


def test_rollout_prepublish_pause_resume_withdraw_and_v1_contract(catalog):
    repository, owner, scoped = catalog
    data = metadata()
    identifier = repository.create_release(owner.id, data)["release_id"]
    with pytest.raises(ControlCenterValidationError):
        repository.set_release_rollout(owner.id, identifier, state="active", percentage=10)
    repository.verify_release(owner.id, identifier, sha256=data["artifact_sha256"], size=data["artifact_size"])
    repository.set_release_rollout(owner.id, identifier, state="paused", percentage=10)
    manifest = repository.publish_release(owner.id, identifier)["release"]
    ReleaseManifest.parse(manifest)
    assert "rollout_state" not in manifest
    assert repository.list_releases()[0]["rollout_state"] == "paused"
    for state, percentage in [("active", 10), ("active", 50), ("paused", 50), ("active", 100)]:
        repository.set_release_rollout(owner.id, identifier, state=state, percentage=percentage)
        release = repository.list_releases()[0]
        assert (release["rollout_state"], release["rollout_percentage"]) == (state, percentage)
    with pytest.raises(ControlCenterValidationError):
        repository.set_release_rollout(scoped.id, identifier, state="withdrawn", percentage=0)
    repository.set_release_rollout(owner.id, identifier, state="withdrawn", percentage=0)
    assert repository.list_releases()[0]["status"] == "withdrawn"
    with pytest.raises(ControlCenterConflictError):
        repository.set_release_rollout(owner.id, identifier, state="active", percentage=100)
    with pytest.raises(ControlCenterValidationError):
        repository.publish_release(owner.id, identifier)


@pytest.mark.parametrize("percentage", [-1, 101, True, 1.5, "50"])
def test_invalid_percentage_rejected(catalog, percentage):
    repository, owner, _ = catalog
    with pytest.raises(ControlCenterValidationError):
        repository.set_release_rollout(owner.id, "unknown", state="active", percentage=percentage)


def test_deterministic_cohorts_are_monotonic_and_bounded():
    ids = [f"installation-{i}" for i in range(1000)]
    cohorts = [{i for i in ids if rollout_eligible(i, "release-1", p)} for p in (0, 10, 50, 100)]
    assert not cohorts[0]
    assert cohorts[0] < cohorts[1] < cohorts[2] < cohorts[3]
    assert len(cohorts[3]) == 1000
    assert 50 < len(cohorts[1]) < 150
    assert cohorts[1] == {i for i in ids if rollout_eligible(i, "release-1", 10)}


def test_distribution_requires_confirmation_csrf_and_admin(control_client, control_app):
    control_login(control_client)
    repository = control_app.state.control_repository
    owner = repository.get_platform_user_by_username(CONTROL_USERNAME)
    data = metadata()
    identifier = repository.create_release(owner.id, data)["release_id"]
    repository.verify_release(owner.id, identifier, sha256=data["artifact_sha256"], size=data["artifact_size"])
    url = f"/versoes/{identifier}/distribuicao"
    assert control_client.post(url, data={"state": "active", "percentage": 10}).status_code == 422
    csrf = control_client.headers.pop("X-CSRF-Token")
    assert control_client.post(url, data={"state": "active", "percentage": 10, "confirmation": "confirmar"}).status_code == 403
    control_client.headers["X-CSRF-Token"] = csrf
    assert control_client.post(url, data={"state": "active", "percentage": 10, "confirmation": "confirmar"}, follow_redirects=False).status_code == 303
    assert repository.list_releases()[0]["rollout_percentage"] == 10
