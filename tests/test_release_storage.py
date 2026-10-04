from io import BytesIO
import json

import pytest

from app.updates.contracts import UpdateValidationError
from control_center.domain import ControlCenterValidationError
from control_center.release_storage import SupabaseReleaseStorage, prepare_official_release
from tests.test_release_catalog import catalog, metadata
from tests.test_update_contracts_download import ARTIFACT, REF


def unsigned(_):
    return {"status": "NotSigned", "thumbprint": None, "sha256": None}


class Response(BytesIO):
    def __init__(self, body, status=200):
        super().__init__(body)
        self.status, self.size = status, len(body)

    def getheader(self, key):
        return str(self.size) if key == "Content-Length" else None


class Connection:
    def __init__(self, response):
        self.response, self.sent, self.headers = response, b"", {}
        self.method, self.path, self.closed = None, None, False

    def putrequest(self, method, path):
        self.method, self.path = method, path

    def putheader(self, key, value):
        self.headers[key] = value

    def endheaders(self):
        pass

    def send(self, block):
        self.sent += block

    def request(self, method, path, headers):
        self.method, self.path, self.headers = method, path, headers

    def getresponse(self):
        return self.response

    def close(self):
        self.closed = True


def storage(*, remote=ARTIFACT, upload_status=200, upload_body=b"{}"):
    connections = [Connection(Response(upload_body, upload_status)), Connection(Response(remote))]
    queue = iter(connections)
    instance = SupabaseReleaseStorage(REF, "sb_secret_" + "q" * 40, connection_factory=lambda: next(queue))
    return instance, connections


def test_publisher_verifies_round_trip_before_marking_verified(catalog, tmp_path):
    repo, owner, _ = catalog
    artifact = tmp_path / "qa.exe"
    artifact.write_bytes(ARTIFACT)
    remote, connections = storage()
    result = prepare_official_release(repo, remote, actor_id=owner.id, artifact=artifact,
                                      metadata=metadata(), signature_reader=unsigned)
    assert result["status"] == "verified" and repo.list_releases()[0]["status"] == "verified"
    assert connections[0].sent == ARTIFACT and connections[0].headers["x-upsert"] == "false"
    assert connections[1].method == "GET" and "/authenticated/" in connections[1].path
    assert all(c.closed for c in connections)
    assert "Authorization" not in connections[0].headers  # opaque server keys use apikey only
    assert "sb_secret_" not in json.dumps(result)


@pytest.mark.parametrize("kwargs", [
    {"remote": b"MZ" + b"x" * (len(ARTIFACT) - 2)}, {"remote": ARTIFACT[:-1]},
    {"upload_status": 302}, {"upload_status": 403},
])
def test_corrupt_storage_and_redirect_never_mark_verified(catalog, tmp_path, kwargs):
    repo, owner, _ = catalog
    artifact = tmp_path / "qa.exe"
    artifact.write_bytes(ARTIFACT)
    remote, _ = storage(**kwargs)
    with pytest.raises((UpdateValidationError, ControlCenterValidationError)):
        prepare_official_release(repo, remote, actor_id=owner.id, artifact=artifact,
                                 metadata=metadata(), signature_reader=unsigned)
    assert repo.list_releases()[0]["status"] == "draft"
    with pytest.raises(ControlCenterValidationError):
        repo.publish_release(owner.id, repo.list_releases()[0]["id"])


def test_duplicate_upload_is_accepted_only_after_verifying_identical_stored_bytes(catalog, tmp_path):
    repo, owner, _ = catalog
    artifact = tmp_path / "qa.exe"
    artifact.write_bytes(ARTIFACT)
    remote, _ = storage(upload_status=400, upload_body=b'{"error":"Duplicate"}')
    assert prepare_official_release(repo, remote, actor_id=owner.id, artifact=artifact,
                                    metadata=metadata(), signature_reader=unsigned)["status"] == "verified"


def test_scoped_operator_cannot_upload(catalog, tmp_path):
    repo, _, scoped = catalog
    remote, connections = storage()
    with pytest.raises(ControlCenterValidationError):
        prepare_official_release(repo, remote, actor_id=scoped.id, artifact=tmp_path / "missing.exe", metadata=metadata())
    assert not repo.list_releases() and all(c.method is None for c in connections)


def test_oversized_artifact_is_rejected_before_storage_request(tmp_path):
    from control_center.release_storage import RELEASE_STORAGE_MAX_BYTES
    remote, connections = storage()
    with pytest.raises(ControlCenterValidationError, match="50 MB"):
        remote.upload_and_verify(tmp_path / "unused.exe", metadata() | {"artifact_size": RELEASE_STORAGE_MAX_BYTES + 1})
    assert all(connection.method is None for connection in connections)
