import pytest

from app.updates.contracts import ReleaseManifest, UpdateValidationError
from app.updates.verification import verify_installable_artifact
from tests.test_update_contracts_download import ARTIFACT, manifest_dict


@pytest.mark.parametrize("length", [40, 64])
def test_authenticode_requires_valid_pinned_publisher(tmp_path, length):
    file = tmp_path / "qa.exe"
    file.write_bytes(ARTIFACT)
    manifest = ReleaseManifest.parse(manifest_dict(code_signing="AUTHENTICODE", signer_thumbprint="A" * length))
    valid = {"status": "Valid", "thumbprint": "A" * 40, "sha256": "A" * 64}
    assert verify_installable_artifact(file, manifest, signature_reader=lambda _: valid) == "AUTHENTICODE"
    for result in ({**valid, "status": "HashMismatch"}, {**valid, "status": "NotTrusted"},
                   {"status": "NotSigned", "thumbprint": None, "sha256": None},
                   {"status": "Valid", "thumbprint": "B" * 40, "sha256": "B" * 64}):
        with pytest.raises(UpdateValidationError):
            verify_installable_artifact(file, manifest, signature_reader=lambda _: result)


def test_pending_signing_does_not_accept_invalid_or_unexpected_signature(tmp_path):
    file = tmp_path / "qa.exe"
    file.write_bytes(ARTIFACT)
    manifest = ReleaseManifest.parse(manifest_dict())
    unsigned = {"status": "NotSigned", "thumbprint": None, "sha256": None}
    assert verify_installable_artifact(file, manifest, signature_reader=lambda _: unsigned) == "CODE_SIGNING_PENDING"
    for status in ("UnknownError", "HashMismatch", "NotTrusted", "Valid"):
        with pytest.raises(UpdateValidationError):
            verify_installable_artifact(file, manifest, signature_reader=lambda _: {**unsigned, "status": status})


def test_signature_query_cannot_hide_artifact_replacement(tmp_path):
    file = tmp_path / "qa.exe"
    file.write_bytes(ARTIFACT)
    def replace(_):
        file.write_bytes(b"MZreplacement")
        return {"status": "NotSigned", "thumbprint": None, "sha256": None}
    with pytest.raises(UpdateValidationError, match="mudou"):
        verify_installable_artifact(file, ReleaseManifest.parse(manifest_dict()), signature_reader=replace)
