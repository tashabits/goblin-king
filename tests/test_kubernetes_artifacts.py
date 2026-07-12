"""Tests for Kubernetes artifact retention and result forwarding."""

from __future__ import annotations

import hashlib
import json
import shutil
import zipfile
from pathlib import Path

import pytest
from redis import Redis

from goblin_king.contracts import ArtifactRecord, GoblinResult
from goblin_king.kubernetes_artifact_config import (
    ArtifactRetentionRequest,
    KubernetesArtifactRetention,
)
from goblin_king.kubernetes_artifacts import (
    retain_result_artifacts,
)
from goblin_king.kubernetes_result_forwarder import (
    RESULT_FORWARDER_SCRIPT,
    ResultForwarderSettings,
    forward_result,
)

PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    b"\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
)


def test_retains_png_and_zip_with_actual_size_digest_and_project_scope(tmp_path: Path) -> None:
    """Copy declared bytes atomically and replace worker-local URIs with durable URIs."""
    source = tmp_path / "source"
    destination = tmp_path / "retained"
    source.mkdir()
    (source / "proof.png").write_bytes(PNG_BYTES)
    with zipfile.ZipFile(source / "proof.zip", "w") as archive:
        archive.writestr("proof.txt", "retained")
    png_digest = hashlib.sha256(PNG_BYTES).hexdigest()
    result = GoblinResult.ok(
        artifacts=[
            ArtifactRecord(name="proof.png", uri="proof.png", media_type="image/png"),
            ArtifactRecord(name="proof.zip", uri="proof.zip", media_type="application/zip"),
        ],
        metrics={"artifact.proof.png.sha256": f"sha256:{png_digest}"},
    )

    retained = retain_result_artifacts(
        result,
        ArtifactRetentionRequest(
            source_root=source,
            destination_root=destination,
            uri_root="/data/artifacts",
            run_id="run-1",
            project_id="project-1",
            max_files=2,
            max_bytes=4096,
        ),
    )

    assert retained.status == "success"
    assert [artifact.name for artifact in retained.artifacts] == ["proof.png", "proof.zip"]
    assert all(
        artifact.uri.startswith("file:///data/artifacts/projects/")
        for artifact in retained.artifacts
    )
    assert "project-1" not in retained.artifacts[0].uri
    assert retained.metrics["artifact.proof.png.bytes"] == len(PNG_BYTES)
    assert retained.metrics["artifact.proof.png.sha256"] == png_digest
    assert retained.metrics["artifact.retained.files"] == 2
    retained_files = sorted(path for path in destination.rglob("*") if path.is_file())
    assert len(retained_files) == 2
    assert {path.suffix for path in retained_files} == {".png", ".zip"}


@pytest.mark.parametrize(
    ("artifact", "max_files", "max_bytes", "message"),
    [
        (ArtifactRecord(name="escape.txt", uri="../escape.txt"), 1, 100, "outside"),
        (ArtifactRecord(name="nested/name.txt", uri="name.txt"), 1, 100, "single path"),
        (ArtifactRecord(name="name.txt", uri="name.txt"), 0, 100, "file count"),
        (ArtifactRecord(name="name.txt", uri="name.txt"), 1, 2, "bytes exceed"),
        (
            ArtifactRecord(name="name.txt", uri="name.txt", media_type="text/plain\r\nX-Bad: 1"),
            1,
            100,
            "media type",
        ),
    ],
)
def test_rejects_unsafe_or_over_policy_artifacts_without_persistable_metadata(
    tmp_path: Path,
    artifact: ArtifactRecord,
    max_files: int,
    max_bytes: int,
    message: str,
) -> None:
    """Convert retention violations into explicit failures with no artifact metadata."""
    source = tmp_path / "source"
    source.mkdir()
    (source / "name.txt").write_text("hello", encoding="utf-8")
    (tmp_path / "escape.txt").write_text("escape", encoding="utf-8")

    retained = retain_result_artifacts(
        GoblinResult.ok(artifacts=[artifact]),
        ArtifactRetentionRequest(
            source_root=source,
            destination_root=tmp_path / "retained",
            uri_root="/data/artifacts",
            run_id="run-rejected",
            max_files=max_files,
            max_bytes=max_bytes,
        ),
    )

    assert retained.status == "failed"
    assert message in (retained.error or "")
    assert retained.artifacts == []
    retained_root = tmp_path / "retained"
    assert not [path for path in retained_root.rglob("*") if path.is_file()]


def test_rejects_digest_mismatch_and_unconfigured_storage(tmp_path: Path) -> None:
    """Never preserve metadata when bytes fail integrity or no durable backend exists."""
    source = tmp_path / "source"
    source.mkdir()
    (source / "proof.txt").write_text("proof", encoding="utf-8")
    result = GoblinResult.ok(
        artifacts=[ArtifactRecord(name="proof.txt", uri="proof.txt")],
        metrics={"artifact.proof.txt.sha256": "0" * 64},
    )
    request = ArtifactRetentionRequest(
        source_root=source,
        destination_root=tmp_path / "retained",
        uri_root="/data/artifacts",
        run_id="run-digest",
    )

    mismatch = retain_result_artifacts(result, request)
    unconfigured = retain_result_artifacts(
        result.model_copy(update={"metrics": {}}),
        request.__class__(
            source_root=source,
            destination_root=None,
            uri_root=None,
            run_id="run-unconfigured",
        ),
    )

    assert mismatch.status == "failed"
    assert "digest does not match" in (mismatch.error or "")
    assert mismatch.artifacts == []
    assert unconfigured.status == "failed"
    assert "not configured" in (unconfigured.error or "")
    assert unconfigured.artifacts == []


def test_retention_is_idempotent_and_preserves_original_failure(tmp_path: Path) -> None:
    """Allow safe forwarder retry without replacing immutable bytes or hiding worker failure."""
    source = tmp_path / "source"
    source.mkdir()
    (source / "failure.txt").write_text("diagnostic", encoding="utf-8")
    result = GoblinResult.failed(
        error="worker failed",
        artifacts=[ArtifactRecord(name="failure.txt", uri="failure.txt")],
    )
    request = ArtifactRetentionRequest(
        source_root=source,
        destination_root=tmp_path / "retained",
        uri_root="/data/artifacts",
        run_id="run-failed",
    )

    first = retain_result_artifacts(result, request)
    second = retain_result_artifacts(result, request)

    assert first.status == second.status == "failed"
    assert first.error == second.error == "worker failed"
    assert first.artifacts == second.artifacts
    assert len([path for path in (tmp_path / "retained").rglob("*") if path.is_file()]) == 1


def test_rejects_symlink_artifacts_when_platform_supports_them(tmp_path: Path) -> None:
    """Do not follow worker-created links even when their target stays inside the source root."""
    source = tmp_path / "source"
    source.mkdir()
    (source / "target.txt").write_text("target", encoding="utf-8")
    link = source / "link.txt"
    try:
        link.symlink_to(source / "target.txt")
    except OSError:
        pytest.skip("symbolic links are not available for this test user")

    retained = retain_result_artifacts(
        GoblinResult.ok(artifacts=[ArtifactRecord(name="link.txt", uri="link.txt")]),
        ArtifactRetentionRequest(
            source_root=source,
            destination_root=tmp_path / "retained",
            uri_root="/data/artifacts",
            run_id="run-link",
        ),
    )

    assert retained.status == "failed"
    assert "symbolic links" in (retained.error or "")


def test_rejects_symlink_directories_when_platform_supports_them(tmp_path: Path) -> None:
    """Inspect unresolved path components instead of only the resolved target path."""
    source = tmp_path / "source"
    target = source / "target"
    target.mkdir(parents=True)
    (target / "proof.txt").write_text("target", encoding="utf-8")
    link = source / "linked-directory"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("symbolic links are not available for this test user")

    retained = retain_result_artifacts(
        GoblinResult.ok(
            artifacts=[ArtifactRecord(name="proof.txt", uri="linked-directory/proof.txt")]
        ),
        ArtifactRetentionRequest(
            source_root=source,
            destination_root=tmp_path / "retained",
            uri_root="/data/artifacts",
            run_id="run-linked-directory",
        ),
    )

    assert retained.status == "failed"
    assert "symbolic links" in (retained.error or "")


def test_forwarder_publishes_only_the_retained_result(tmp_path: Path) -> None:
    """Place durable artifact URIs in Redis only after the bytes are copied."""
    source = tmp_path / "source"
    source.mkdir()
    (source / "proof.txt").write_text("forwarded", encoding="utf-8")
    result_path = tmp_path / "result.json"
    result_path.write_text(
        GoblinResult.ok(
            artifacts=[ArtifactRecord(name="proof.txt", uri="proof.txt", media_type="text/plain")]
        ).model_dump_json(),
        encoding="utf-8",
    )
    calls: list[tuple[str, str, int]] = []

    class FakeRedis:
        def set(self, key: str, payload: str, *, ex: int) -> None:
            calls.append((key, payload, ex))

    forwarded = forward_result(
        ResultForwarderSettings(
            run_id="run-forwarded",
            redis_url="redis://example/0",
            result_path=result_path,
            wait_seconds=1,
        ),
        environ={
            "GOBLIN_ARTIFACT_SOURCE_ROOT": str(source),
            "GOBLIN_ARTIFACT_DESTINATION_ROOT": str(tmp_path / "retained"),
            "GOBLIN_KING_K8S_ARTIFACT_URI_ROOT": "/data/artifacts",
            "GOBLIN_ARTIFACT_PROJECT_ID": "project-1",
        },
        redis_factory=lambda _url: FakeRedis(),
    )

    assert forwarded.status == "success"
    assert len(calls) == 1
    published = json.loads(calls[0][1])
    assert published["artifacts"] == [forwarded.artifacts[0].model_dump(mode="json")]
    retained_path = next(path for path in (tmp_path / "retained").rglob("*") if path.is_file())
    shutil.rmtree(source)
    assert retained_path.read_text(encoding="utf-8") == "forwarded"


def test_legacy_forwarder_rejects_unretained_artifact_metadata(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Keep the Python-plus-Redis image contract without publishing lost artifact claims."""
    result_path = tmp_path / "result.json"
    result_path.write_text(
        GoblinResult.ok(
            artifacts=[ArtifactRecord(name="lost.txt", uri="/artifacts/lost.txt")],
            metrics={"artifact.lost.txt.bytes": 4, "ordinary": 1},
        ).model_dump_json(),
        encoding="utf-8",
    )
    published: dict[str, str] = {}

    class FakeRedis:
        @staticmethod
        def set(key: str, value: str, *, ex: int) -> None:
            published.update({"key": key, "value": value, "expiry": str(ex)})

    monkeypatch.setattr(Redis, "from_url", lambda _url: FakeRedis())
    monkeypatch.setenv("GOBLIN_RUN_ID", "run-legacy")
    monkeypatch.setenv("GOBLIN_REDIS_URL", "redis://unused")
    monkeypatch.setenv("GOBLIN_RESULT_PATH", str(result_path))

    with pytest.raises(SystemExit) as exit_info:
        exec(RESULT_FORWARDER_SCRIPT, {})

    forwarded = GoblinResult.model_validate_json(published["value"])
    assert exit_info.value.code == 0
    assert forwarded.status == "failed"
    assert forwarded.artifacts == []
    assert forwarded.metrics == {"ordinary": 1}
    assert "not configured" in (forwarded.error or "")


def test_kubernetes_retention_configuration_is_validated_and_environment_driven() -> None:
    """Keep scheduler configuration additive while rejecting unsafe volume paths and claims."""
    config = KubernetesArtifactRetention.from_environment(
        {
            "GOBLIN_KING_K8S_ARTIFACT_PVC_CLAIM": "release-data",
            "GOBLIN_KING_K8S_ARTIFACT_VOLUME_SUBDIRECTORY": "artifacts/retained",
            "GOBLIN_KING_K8S_ARTIFACT_URI_ROOT": "/data/artifacts",
        }
    )

    assert config is not None
    assert config.destination_root == "/goblin-retained-artifacts"
    assert KubernetesArtifactRetention.from_environment({}) is None
    with pytest.raises(ValueError, match="Kubernetes resource name"):
        KubernetesArtifactRetention(claim_name="../claim")
    with pytest.raises(ValueError, match="relative path"):
        KubernetesArtifactRetention(claim_name="claim", volume_subdirectory="../artifacts")
