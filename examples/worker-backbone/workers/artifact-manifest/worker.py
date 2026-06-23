"""Deterministic artifact-producing worker for the portable backbone example."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

KIND = "example.worker-backbone.artifact-manifest"


def build_manifest(payload: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    """Build a stable manifest from local input data."""
    items = [
        {
            "name": str(item["name"]),
            "bytes": int(item["bytes"]),
        }
        for item in payload.get("items", [])
    ]
    items.sort(key=lambda item: item["name"])
    manifest = {
        "dataset": str(payload.get("dataset", "local-fixture")),
        "items": items,
        "run_id": context.get("run_id"),
    }
    if payload.get("include_totals", True):
        manifest["totals"] = {
            "files": len(items),
            "bytes": sum(item["bytes"] for item in items),
        }
    return manifest


def build_result(
    payload: dict[str, Any],
    context: dict[str, Any],
    artifact_root: Path,
) -> dict[str, Any]:
    """Write the manifest artifact and return its result envelope."""
    artifact_root.mkdir(parents=True, exist_ok=True)
    manifest = build_manifest(payload, context)
    artifact_body = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    artifact_path = artifact_root / "manifest.json"
    artifact_path.write_text(artifact_body, encoding="utf-8")
    artifact_bytes = len(artifact_body.encode("utf-8"))

    totals = manifest.get("totals", {"files": len(manifest["items"]), "bytes": 0})
    return {
        "status": "success",
        "data": {
            "kind": KIND,
            "dataset": manifest["dataset"],
            "manifest": "manifest.json",
            "files": totals["files"],
            "bytes": totals["bytes"],
        },
        "artifacts": [
            {
                "name": "manifest.json",
                "uri": "manifest.json",
                "media_type": "application/json",
            }
        ],
        "metrics": {
            "manifest.items": totals["files"],
            "manifest.input_bytes": totals["bytes"],
            "artifact.manifest.json.bytes": artifact_bytes,
        },
        "handoff": [],
        "error": None,
    }


def main() -> None:
    """Read contract files, write an artifact, and write the result envelope."""
    payload = _read_json_env("GOBLIN_INPUT_PATH")
    context = _read_json_env("GOBLIN_CONTEXT_PATH")
    artifact_root = Path(os.environ["GOBLIN_ARTIFACT_ROOT"])
    result = build_result(payload, context, artifact_root)
    _write_json(Path(os.environ["GOBLIN_RESULT_PATH"]), result)


def _read_json_env(name: str) -> dict[str, Any]:
    path = Path(os.environ[name])
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


if __name__ == "__main__":
    main()
