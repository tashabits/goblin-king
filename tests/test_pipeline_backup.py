"""Recovery rejects incomplete or altered image graphs before mounting restored state."""

import hashlib
import io
import json
import tarfile
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from scripts.pipeline_storage_recovery import restore_artifact_backup, verify_artifact_backup


def archive(tmp_path, *, corrupt=False, missing=False, unsafe=False, publication=None):
    blob = b"preserved image layer"
    digest = "sha256:" + hashlib.sha256(blob).hexdigest()
    manifest = json.dumps({"config": {"digest": digest, "size": len(blob)}, "layers": []}).encode()
    image = "sha256:" + hashlib.sha256(manifest).hexdigest()
    entries = {
        "artifacts/index.json": json.dumps(
            {"images": [{"digest": image, **(publication or {})}]}
        ).encode(),
        "artifacts/blobs/sha256/" + image[7:]: manifest,
    }
    if not missing:
        entries["artifacts/blobs/sha256/" + digest[7:]] = b"x" * len(blob) if corrupt else blob
    if unsafe:
        entries["artifacts/../../outside"] = b"escape"
    path = tmp_path / "artifacts.tar"
    with tarfile.open(path, "w") as result:
        for name, raw in entries.items():
            info = tarfile.TarInfo(name)
            info.size = len(raw)
            result.addfile(info, io.BytesIO(raw))
    (tmp_path / "artifacts.json").write_text(
        json.dumps(
            {
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "images": 1,
                "state_sha256": "a" * 64,
            }
        )
    )
    return image


def test_complete_image_graph_can_be_verified_without_registry_access(tmp_path):
    image = archive(tmp_path)
    assert verify_artifact_backup(tmp_path)["images"] == [{"digest": image}]


@pytest.mark.parametrize("fault", ["corrupt", "missing", "unsafe"])
def test_archive_checksum_alone_does_not_prove_usable_images(tmp_path, fault):
    archive(tmp_path, **{fault: True})
    with pytest.raises(RuntimeError, match="checksum|missing|unsafe"):
        verify_artifact_backup(tmp_path)


def test_changed_archive_is_rejected_before_contents_are_read(tmp_path):
    archive(tmp_path)
    with (tmp_path / "artifacts.tar").open("ab") as stream:
        stream.write(b"changed")
    with pytest.raises(RuntimeError, match="backup checksum"):
        verify_artifact_backup(tmp_path)


def test_images_from_another_state_snapshot_cannot_be_used_for_restore(tmp_path):
    archive(tmp_path)
    with pytest.raises(RuntimeError, match="different state snapshot"):
        verify_artifact_backup(tmp_path, state_sha256="b" * 64)


def test_restoration_requires_destination_and_reads_back_blob_bytes(tmp_path):
    publication = {
        "repository": "example/runtime",
        "build_id": "12345678-1234-1234-1234-123456789abc",
    }
    digest = archive(tmp_path, publication=publication)
    client = Mock(endpoint="https://registry.invalid", authorization="fixture")
    client.manifest.return_value = digest
    client.opener = SimpleNamespace(
        open=lambda *args, **kwargs: io.BytesIO(b"preserved image layer")
    )
    with pytest.raises(RuntimeError, match="no approved"):
        restore_artifact_backup(tmp_path, {}, state_sha256="a" * 64)
    client.blob.assert_not_called()
    result = restore_artifact_backup(tmp_path, {"example/runtime": client}, state_sha256="a" * 64)
    assert result["images"] == [{**publication, "digest": digest, "verified": True}]
    client.opener.open = lambda *args, **kwargs: io.BytesIO(b"corrupted image layer")
    with pytest.raises(RuntimeError, match="checksum"):
        restore_artifact_backup(tmp_path, {"example/runtime": client}, state_sha256="a" * 64)


def test_corrupt_backup_cannot_write_to_registry(tmp_path):
    archive(
        tmp_path,
        corrupt=True,
        publication={
            "repository": "example/runtime",
            "build_id": "12345678-1234-1234-1234-123456789abc",
        },
    )
    client = Mock()
    with pytest.raises(RuntimeError, match="checksum"):
        restore_artifact_backup(tmp_path, {"example/runtime": client}, state_sha256="a" * 64)
    client.blob.assert_not_called()
    client.manifest.assert_not_called()
