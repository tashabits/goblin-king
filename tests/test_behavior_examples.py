from pathlib import Path

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
