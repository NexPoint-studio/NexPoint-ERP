from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import sqlite3

import pytest

from control_center.domain import ControlCenterConflictError, ControlCenterValidationError, PlatformUser
from control_center.local_repository import LocalControlCenterRepository
from control_center.releases import METADATA_FIELDS
from tests.test_update_contracts_download import manifest_dict
from tests.test_control_center import CONTROL_USERNAME, control_app, control_client, control_login


def metadata(**changes):
    return {k: v for k, v in manifest_dict(**changes).items() if k in METADATA_FIELDS}


@pytest.fixture()
def catalog(tmp_path):
    repository = LocalControlCenterRepository(tmp_path / "control.sqlite3")
    owner = repository.save_platform_user(PlatformUser(username="release-admin", display_name="QA", role="platform_admin"),
                                          password="synthetic-release-admin-test")
    scoped = repository.save_platform_user(PlatformUser(username="release-support", display_name="QA support", role="nexpoint_control_admin"),
                                           password="synthetic-release-support-test")
    yield repository, owner, scoped
    repository.close()


def test_release_lifecycle_persists_and_publishing_is_idempotent(catalog):
    repository, owner, _ = catalog
    data = metadata()
    first = repository.create_release(owner.id, data)
    assert first == repository.create_release(owner.id, data)
    with pytest.raises(ControlCenterConflictError):
        repository.create_release(owner.id, metadata(artifact_size=1))
    identifier = first["release_id"]
    with pytest.raises(ControlCenterValidationError):
        repository.publish_release(owner.id, identifier)
    with pytest.raises(ControlCenterValidationError):
        repository.verify_release(owner.id, identifier, sha256="f" * 64, size=data["artifact_size"])
    repository.verify_release(owner.id, identifier, sha256=data["artifact_sha256"], size=data["artifact_size"])
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: repository.publish_release(owner.id, identifier), range(2)))
    assert results[0] == results[1]
    assert len(repository.list_releases()) == 1
    assert repository.list_releases()[0]["status"] == "published"
    with sqlite3.connect(repository.database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM release_catalog_events").fetchone() == (3,)
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    reopened = LocalControlCenterRepository(repository.database_path)
    assert reopened.list_releases() == repository.list_releases()
    reopened.close()


def test_catalog_blocks_scoped_inactive_actors_and_downgrade(catalog):
    repository, owner, scoped = catalog
    data = metadata()
    for actor in (scoped.id, "unknown"):
        with pytest.raises(ControlCenterValidationError):
            repository.create_release(actor, data)
    newer = repository.create_release(owner.id, data)["release_id"]
    for actor in (scoped.id, "unknown"):
        with pytest.raises(ControlCenterValidationError):
            repository.verify_release(actor, newer, sha256=data["artifact_sha256"], size=data["artifact_size"])
        with pytest.raises(ControlCenterValidationError):
            repository.publish_release(actor, newer)
    repository.verify_release(owner.id, newer, sha256=data["artifact_sha256"], size=data["artifact_size"])
    repository.publish_release(owner.id, newer)
    older = repository.create_release(owner.id, metadata(version="1.1.5"))["release_id"]
    repository.verify_release(owner.id, older, sha256=data["artifact_sha256"], size=data["artifact_size"])
    with pytest.raises(ControlCenterConflictError):
        repository.publish_release(owner.id, older)
    repository.save_platform_user(replace(owner, active=False))
    with pytest.raises(ControlCenterValidationError):
        repository.create_release(owner.id, metadata(version="1.3.0"))


def test_versions_page_only_exposes_publish_after_verification_and_requires_csrf(control_client, control_app):
    control_login(control_client)
    repository = control_app.state.control_repository
    owner = repository.get_platform_user_by_username(CONTROL_USERNAME)
    data = metadata(release_notes="QA <script>unsafe()</script>")
    identifier = repository.create_release(owner.id, data)["release_id"]
    url = f"/versoes/{identifier}/publicar"
    page = control_client.get("/versoes")
    assert page.status_code == 200
    assert url not in page.text
    assert "<script>unsafe()</script>" not in page.text
    assert "&lt;script&gt;" in page.text
    assert control_client.post(url, data={"confirmation": "publicar"}).status_code == 409
    repository.verify_release(owner.id, identifier, sha256=data["artifact_sha256"], size=data["artifact_size"])
    assert url in control_client.get("/versoes").text
    assert control_client.post(url, data={"confirmation": ""}).status_code == 422
    csrf = control_client.headers.pop("X-CSRF-Token")
    assert control_client.post(url, data={"confirmation": "publicar"}).status_code == 403
    control_client.headers["X-CSRF-Token"] = csrf
    response = control_client.post(url, data={"confirmation": "publicar"}, follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/versoes"
    assert "Publicado" in control_client.get("/versoes").text
