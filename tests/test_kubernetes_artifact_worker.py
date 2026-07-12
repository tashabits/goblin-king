"""Tests for the PNG/ZIP Kubernetes artifact proof worker mode."""

from __future__ import annotations

import hashlib
import runpy
import zipfile
from pathlib import Path


def test_artifact_worker_builds_digest_declared_png_and_zip_bundle(tmp_path: Path) -> None:
    """Provide deterministic binary files for live retention and download proof."""
    worker = runpy.run_path("workers/example.artifact/worker.py")

    data, artifacts, metrics = worker["_build_artifacts"](
        {"body": "retained proof", "proof_bundle": True},
        tmp_path,
    )

    assert data == {"message": "artifact proof bundle created", "artifact_count": 2}
    assert [artifact["media_type"] for artifact in artifacts] == [
        "image/png",
        "application/zip",
    ]
    png_path = tmp_path / "artifact-proof.png"
    zip_path = tmp_path / "artifact-proof.zip"
    assert png_path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    with zipfile.ZipFile(zip_path) as archive:
        assert archive.testzip() is None
        assert archive.read("artifact-proof.txt") == b"retained proof"
    for path in (png_path, zip_path):
        assert metrics[f"artifact.{path.name}.bytes"] == path.stat().st_size
        assert metrics[f"artifact.{path.name}.sha256"] == hashlib.sha256(
            path.read_bytes()
        ).hexdigest()


def test_artifact_worker_preserves_default_text_result(tmp_path: Path) -> None:
    """Keep the existing example behavior unchanged unless proof_bundle is requested."""
    worker = runpy.run_path("workers/example.artifact/worker.py")

    data, artifacts, metrics = worker["_build_artifacts"]({"body": "existing"}, tmp_path)

    assert data == {"message": "artifact created", "bytes": 8}
    assert artifacts == [
        {
            "name": "artifact-proof.txt",
            "uri": "artifact-proof.txt",
            "media_type": "text/plain",
        }
    ]
    assert metrics == {}
    assert (tmp_path / "artifact-proof.txt").read_text(encoding="utf-8") == "existing"
