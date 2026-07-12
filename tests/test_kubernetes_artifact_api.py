"""API integration proof for retained Kubernetes artifact bytes."""

from __future__ import annotations

import hashlib
import shutil
from datetime import UTC, datetime
from pathlib import Path

from goblin_king.contracts import ArtifactRecord, GoblinResult, JobRecord, RunRecord
from goblin_king.kubernetes_artifact_config import ArtifactRetentionRequest
from goblin_king.kubernetes_artifacts import retain_result_artifacts
from tests.api_helpers import auth_headers, build_api_client


def test_downloads_retained_bytes_after_transient_worker_storage_is_deleted(
    tmp_path: Path,
) -> None:
    """Serve retained bytes, verify their digest, then apply the existing cleanup policy."""
    client, store, artifact_root = build_api_client(tmp_path)
    source_root = tmp_path / "pod-empty-dir"
    source_root.mkdir()
    png_bytes = b"\x89PNG\r\n\x1a\nretained-proof"
    zip_bytes = b"PK\x03\x04retained-zip-proof"
    (source_root / "proof.png").write_bytes(png_bytes)
    (source_root / "proof.zip").write_bytes(zip_bytes)
    retained = retain_result_artifacts(
        GoblinResult.ok(
            artifacts=[
                ArtifactRecord(name="proof.png", uri="proof.png", media_type="image/png"),
                ArtifactRecord(name="proof.zip", uri="proof.zip", media_type="application/zip"),
            ]
        ),
        ArtifactRetentionRequest(
            source_root=source_root,
            destination_root=artifact_root,
            uri_root=artifact_root.resolve().as_uri(),
            run_id="run-retained",
            project_id="project-1",
            max_files=2,
            max_bytes=1024,
        ),
    )
    shutil.rmtree(source_root)
    now = datetime(2026, 7, 12, tzinfo=UTC)
    store.save_job(
        JobRecord(
            id="job-retained",
            kind="example.artifact",
            input={},
            created_at=now,
            status="completed",
            project_id="project-1",
        )
    )
    store.save_run(
        RunRecord(
            id="run-retained",
            job_id="job-retained",
            kind="example.artifact",
            status="completed",
            started_at=now,
            finished_at=now,
            project_id="project-1",
            result=retained,
        )
    )

    listed = client.get("/runs/run-retained/artifacts", headers=auth_headers())
    downloaded_png = client.get(
        "/runs/run-retained/artifacts/proof.png",
        headers=auth_headers(),
    )
    downloaded_zip = client.get(
        "/runs/run-retained/artifacts/proof.zip",
        headers=auth_headers(),
    )

    assert listed.status_code == 200
    assert [item["name"] for item in listed.json()] == ["proof.png", "proof.zip"]
    assert downloaded_png.content == png_bytes
    assert downloaded_zip.content == zip_bytes
    assert hashlib.sha256(downloaded_png.content).hexdigest() == retained.metrics[
        "artifact.proof.png.sha256"
    ]
    assert hashlib.sha256(downloaded_zip.content).hexdigest() == retained.metrics[
        "artifact.proof.zip.sha256"
    ]

    cleaned = client.post(
        "/admin/artifacts/cleanup",
        json={"dry_run": False, "project_id": "project-1", "max_total_bytes": 0},
        headers=auth_headers(),
    )

    assert cleaned.status_code == 200
    assert cleaned.json()["files_selected"] == 2
    assert (
        client.get(
            "/runs/run-retained/artifacts/proof.png",
            headers=auth_headers(),
        ).status_code
        == 404
    )
