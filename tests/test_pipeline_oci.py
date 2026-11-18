"""Untrusted image archives cannot substitute provenance or escape the OCI layout."""

import hashlib
import io
import json
import tarfile

import pytest

from goblin_king.pipeline_oci import MANIFEST_TYPE, validate_oci


def artifact(tmp_path, *, extra=None, corrupt=False):
    entries = {}

    def blob(raw, media_type):
        digest = "sha256:" + hashlib.sha256(raw).hexdigest()
        entries["blobs/sha256/" + digest[7:]] = raw
        return {"digest": digest, "size": len(raw), "mediaType": media_type}

    config = blob(
        json.dumps(
            {
                "os": "linux",
                "architecture": "amd64",
                "config": {"Labels": {"io.goblin-king.build-id": "approved"}},
            }
        ).encode(),
        "application/vnd.oci.image.config.v1+json",
    )
    manifest = blob(
        json.dumps({"schemaVersion": 2, "config": config, "layers": []}).encode(), MANIFEST_TYPE
    )
    entries["oci-layout"] = b'{"imageLayoutVersion":"1.0.0"}'
    entries["index.json"] = json.dumps({"schemaVersion": 2, "manifests": [manifest]}).encode()
    if corrupt:
        entries["blobs/sha256/" + config["digest"][7:]] = b"x" * config["size"]
    path = tmp_path / "image.tar"
    with tarfile.open(path, "w") as archive:
        for name, raw in entries.items():
            entry = tarfile.TarInfo(name)
            entry.size = len(raw)
            archive.addfile(entry, io.BytesIO(raw))
        if extra:
            archive.addfile(extra)
    return path


def test_valid_archive_has_exact_manifest_and_build_provenance(tmp_path):
    result = validate_oci(
        artifact(tmp_path), expected_labels={"io.goblin-king.build-id": "approved"}
    )
    assert result["digest"] == "sha256:" + hashlib.sha256(result["manifest"]).hexdigest()


def test_wrong_build_identity_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="provenance"):
        validate_oci(artifact(tmp_path), expected_labels={"io.goblin-king.build-id": "other"})


def test_changed_blob_is_rejected_even_when_size_matches(tmp_path):
    with pytest.raises(ValueError, match="checksum"):
        validate_oci(artifact(tmp_path, corrupt=True), expected_labels={})


@pytest.mark.parametrize("name", ["../../outside", "/absolute", "index.json"])
def test_unsafe_or_duplicate_archive_paths_are_rejected(tmp_path, name):
    with pytest.raises(ValueError, match="unsafe|duplicate"):
        validate_oci(artifact(tmp_path, extra=tarfile.TarInfo(name)), expected_labels={})


def test_symbolic_link_is_rejected(tmp_path):
    link = tarfile.TarInfo("blobs/link")
    link.type, link.linkname = tarfile.SYMTYPE, "/etc/passwd"
    with pytest.raises(ValueError, match="unsafe"):
        validate_oci(artifact(tmp_path, extra=link), expected_labels={})
