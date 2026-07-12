"""Self-contained Docker worker for the example.artifact goblin."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from redis import Redis

KIND = "example.artifact"
PROOF_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42Y"
    "AAAAASUVORK5CYII="
)


def main() -> None:
    """Write a proof artifact and publish its result envelope."""
    input_payload = json.loads(Path(os.environ["GOBLIN_INPUT_PATH"]).read_text(encoding="utf-8"))
    run_id = os.environ["GOBLIN_RUN_ID"]
    worker_id = os.environ["GOBLIN_WORKER_ID"]
    job_id = os.environ.get("GOBLIN_JOB_ID") or None
    artifact_root = Path(os.environ["GOBLIN_ARTIFACT_ROOT"])
    artifact_root.mkdir(parents=True, exist_ok=True)

    _heartbeat("running", worker_id, run_id, job_id)
    data, artifacts, metrics = _build_artifacts(input_payload, artifact_root)
    _publish_result(
        run_id,
        {
            "status": "success",
            "data": data,
            "artifacts": artifacts,
            "metrics": metrics,
            "handoff": [],
            "error": None,
        },
    )
    _heartbeat("completed", worker_id, run_id, job_id)


def _build_artifacts(input_payload: dict, artifact_root: Path) -> tuple[dict, list[dict], dict]:
    """Create the stable text example or the PNG/ZIP retention proof bundle."""
    body = str(input_payload.get("body") or "Goblin King artifact proof")
    if not input_payload.get("proof_bundle"):
        artifact_path = artifact_root / "artifact-proof.txt"
        artifact_path.write_text(body, encoding="utf-8")
        return (
            {"message": "artifact created", "bytes": len(body.encode("utf-8"))},
            [
                {
                    "name": artifact_path.name,
                    "uri": artifact_path.name,
                    "media_type": "text/plain",
                }
            ],
            {},
        )

    png_path = artifact_root / "artifact-proof.png"
    zip_path = artifact_root / "artifact-proof.zip"
    png_path.write_bytes(PROOF_PNG)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("artifact-proof.txt", body)
    paths = [png_path, zip_path]
    artifacts = [
        {
            "name": png_path.name,
            "uri": png_path.name,
            "media_type": "image/png",
        },
        {
            "name": zip_path.name,
            "uri": zip_path.name,
            "media_type": "application/zip",
        },
    ]
    metrics = {
        key: value
        for path in paths
        for key, value in {
            f"artifact.{path.name}.bytes": path.stat().st_size,
            f"artifact.{path.name}.sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }.items()
    }
    return (
        {"message": "artifact proof bundle created", "artifact_count": len(artifacts)},
        artifacts,
        metrics,
    )


def _publish_result(run_id: str, result: dict) -> None:
    result_json = json.dumps(result)
    result_path = Path(os.environ["GOBLIN_RESULT_PATH"])
    result_path.write_text(result_json, encoding="utf-8")
    Redis.from_url(os.environ["GOBLIN_REDIS_URL"]).set(
        f"goblin-king:results:{run_id}",
        result_json,
        ex=3600,
    )


def _heartbeat(status: str, worker_id: str, run_id: str, job_id: str | None) -> None:
    payload = {
        "owner_id": worker_id,
        "owner_type": "worker",
        "status": status,
        "last_seen_at": datetime.now(UTC).isoformat(),
        "job_id": job_id,
        "run_id": run_id,
        "payload": {"kind": KIND},
    }
    encoded = json.dumps(payload)
    client = Redis.from_url(os.environ["GOBLIN_HEARTBEAT_REDIS_URL"])
    client.rpush(os.environ["GOBLIN_HEARTBEAT_KEY"], encoded)
    client.publish(os.environ["GOBLIN_HEARTBEAT_CHANNEL"], encoded)


if __name__ == "__main__":
    main()
