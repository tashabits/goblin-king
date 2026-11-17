"""Image-index aliases may differ only outside the exact selected runtime manifest."""

import hashlib
import json

import pytest

from scripts.pipeline_image_cache import verify_platform_manifests

PLATFORM = {"operatingSystem": "linux", "architecture": "amd64"}


def graph():
    blobs = {}

    def add(document):
        raw = json.dumps(document, sort_keys=True).encode()
        digest = "sha256:" + hashlib.sha256(raw).hexdigest()
        blobs[digest] = raw
        return digest

    runtime = add({"schemaVersion": 2, "config": {"digest": "sha256:" + "a" * 64}, "layers": []})

    def index(target, annotation, *, copies=1):
        return add(
            {
                "schemaVersion": 2,
                "annotations": {"proof": annotation},
                "manifests": [
                    {"digest": target, "platform": {"os": "linux", "architecture": "amd64"}}
                ] * copies,
            }
        )

    return blobs, add, runtime, index


def test_index_alias_requires_identical_hash_verified_runtime():
    blobs, add, runtime, index = graph()
    expected, observed = index(runtime, "new"), index(runtime, "old")
    proof = verify_platform_manifests(blobs.__getitem__, expected, observed, PLATFORM)
    assert proof["platform_manifest"] == runtime
    assert set(proof["manifests"]) == {runtime, expected, observed}
    other = add({"schemaVersion": 2, "config": {"digest": "sha256:" + "b" * 64}, "layers": []})
    with pytest.raises(RuntimeError, match="runtime differs"):
        verify_platform_manifests(blobs.__getitem__, expected, index(other, "old"), PLATFORM)
    blobs[observed] += b" "
    with pytest.raises(RuntimeError, match="checksum"):
        verify_platform_manifests(blobs.__getitem__, expected, observed, PLATFORM)


@pytest.mark.parametrize("copies", [0, 2])
def test_missing_or_ambiguous_platform_is_rejected(copies):
    blobs, _, runtime, index = graph()
    expected = index(runtime, "invalid", copies=copies)
    with pytest.raises(RuntimeError, match="exactly one"):
        verify_platform_manifests(blobs.__getitem__, expected, runtime, PLATFORM)


def test_nested_indexes_are_bounded():
    blobs, _, runtime, index = graph()
    expected = runtime
    for i in range(6):
        expected = index(expected, str(i))
    with pytest.raises(RuntimeError, match="nested"):
        verify_platform_manifests(blobs.__getitem__, expected, runtime, PLATFORM)
