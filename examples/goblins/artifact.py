"""Artifact-producing sample goblin used to prove artifact metadata and downloads."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from goblin_king.contracts import ArtifactRecord, GoblinContext, GoblinResult

GOBLIN_KIND = "example.artifact"


def run(input_payload: dict[str, Any], ctx: GoblinContext) -> GoblinResult:
    """Write a small text artifact and return metadata for the persisted run."""
    artifact_root = Path(ctx.artifact_root)
    artifact_root.mkdir(parents=True, exist_ok=True)
    body = str(input_payload.get("body") or "Goblin King artifact proof")
    artifact_path = artifact_root / "artifact-proof.txt"
    artifact_path.write_text(body, encoding="utf-8")
    return GoblinResult.ok(
        data={"message": "artifact created", "bytes": len(body.encode("utf-8"))},
        artifacts=[
            ArtifactRecord(
                name="artifact-proof.txt",
                uri=str(artifact_path),
                media_type="text/plain",
            )
        ],
    )
