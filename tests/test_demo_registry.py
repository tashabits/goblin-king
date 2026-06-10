from pathlib import Path

from fastapi.testclient import TestClient

from goblin_king.api import create_app
from goblin_king.api_settings import ApiSettings
from goblin_king.registry import GoblinRegistry
from goblin_king.workers import WorkerImageMap

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "demo-goblins.json"
IMAGES = ROOT / "demo-images.json"

LANGUAGE_KINDS = {
    "example.hello-dotnet",
    "example.hello-go",
    "example.hello-java",
    "example.hello-node",
    "example.hello-php",
    "example.hello-python",
    "example.hello-ruby",
    "example.hello-rust",
    "example.hello-shell",
    "example.wasi-c-hello",
    "example.wasi-rust-hello",
}

BEHAVIOR_KINDS = {
    "example.behavior-go-transform",
    "example.behavior-node-artifact",
    "example.behavior-python-progress",
    "example.behavior-python-slow-cancellable",
    "example.behavior-shell-failure",
    "example.behavior-wasi-c-context",
}

CORE_KINDS = {
    "example.artifact",
    "example.controlled-failure",
    "example.echo",
    "example.environment",
    "example.hello",
    "example.long-hello",
    "example.progress",
}


def test_demo_registry_covers_core_language_and_behavior_goblins() -> None:
    registry = GoblinRegistry.from_path(REGISTRY)
    worker_map = WorkerImageMap.from_path(IMAGES)

    kinds = {definition.kind for definition in registry.list()}

    assert CORE_KINDS | LANGUAGE_KINDS | BEHAVIOR_KINDS == kinds
    for definition in registry.list():
        worker = worker_map.get(definition.kind)
        context = worker_map.resolved_context(worker)
        assert context.is_dir()
        assert (context / worker.dockerfile).is_file()


def test_default_api_settings_expose_language_goblins_to_admin(tmp_path: Path) -> None:
    settings = ApiSettings(
        registry=REGISTRY,
        images=IMAGES,
        db=tmp_path / "api.sqlite3",
        artifact_root=tmp_path / "artifacts",
        auth_token="test-token",
    )
    client = TestClient(create_app(settings))

    response = client.get("/goblins", headers={"Authorization": "Bearer test-token"})

    assert response.status_code == 200
    goblins = {item["kind"]: item for item in response.json()}
    for kind in LANGUAGE_KINDS:
        assert goblins[kind]["worker_mapped"] is True
