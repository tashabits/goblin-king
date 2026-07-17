import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from goblin_king.registry import GoblinRegistry
from goblin_king.workers import WorkerImageMap

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "examples" / "behavior-goblins.json"
IMAGES = ROOT / "examples" / "behavior-images.json"

EXPECTED_KINDS = {
    "example.behavior-go-transform",
    "example.behavior-node-artifact",
    "example.behavior-python-progress",
    "example.behavior-python-slow-cancellable",
    "example.behavior-shell-failure",
    "example.behavior-wasi-c-context",
}


def test_behavior_registry_and_images_cover_expected_examples() -> None:
    registry = GoblinRegistry.from_path(REGISTRY)
    worker_map = WorkerImageMap.from_path(IMAGES)

    assert {definition.kind for definition in registry.list()} == EXPECTED_KINDS
    for definition in registry.list():
        worker = worker_map.get(definition.kind)
        context = worker_map.resolved_context(worker)
        assert definition.module == "examples.goblins.container_only"
        assert context.is_dir()
        assert (context / worker.dockerfile).is_file()


def test_behavior_examples_reference_contract_outputs() -> None:
    required_terms = {"GOBLIN_INPUT_PATH", "GOBLIN_CONTEXT_PATH", "GOBLIN_RESULT_PATH"}
    worker_map = WorkerImageMap.from_path(IMAGES)
    for _, worker in worker_map.items():
        context = worker_map.resolved_context(worker)
        source_text = "\n".join(
            path.read_text(encoding="utf-8")
            for path in context.rglob("*")
            if path.is_file() and path.name != "README.md"
        )
        for term in required_terms:
            assert term in source_text, f"{worker.image} does not reference {term}"
        assert "status" in source_text


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_node_artifact_example_emits_portable_local_file_uri(tmp_path: Path) -> None:
    """Keep the example artifact compatible with Docker and Kubernetes retention."""
    input_path = tmp_path / "input.json"
    context_path = tmp_path / "context.json"
    result_path = tmp_path / "result.json"
    artifact_root = tmp_path / "artifacts"
    input_path.write_text(json.dumps({"name": "URI proof"}), encoding="utf-8")
    context_path.write_text(json.dumps({"run_id": "example-proof"}), encoding="utf-8")
    environment = {
        **os.environ,
        "GOBLIN_INPUT_PATH": str(input_path),
        "GOBLIN_CONTEXT_PATH": str(context_path),
        "GOBLIN_RESULT_PATH": str(result_path),
        "GOBLIN_ARTIFACT_ROOT": str(artifact_root),
    }

    completed = subprocess.run(
        ["node", str(ROOT / "examples/goblins/behavior-node-artifact/worker.mjs")],
        check=False,
        capture_output=True,
        env=environment,
        text=True,
        timeout=10,
    )

    assert completed.returncode == 0, completed.stderr
    result = json.loads(result_path.read_text(encoding="utf-8"))
    artifact_path = (artifact_root / "node-artifact.txt").resolve()
    assert result["artifacts"] == [
        {
            "name": "node-artifact.txt",
            "uri": artifact_path.as_uri(),
            "media_type": "text/plain",
        }
    ]
    assert artifact_path.read_text(encoding="utf-8").startswith("Hello URI proof.")
