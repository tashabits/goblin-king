from pathlib import Path

from fastapi.testclient import TestClient

from goblin_king.api import create_app
from goblin_king.api_settings import ApiSettings
from goblin_king.registry import GoblinRegistry
from goblin_king.workers import WorkerImageMap

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "examples" / "cross-language-goblins.json"
IMAGES = ROOT / "examples" / "cross-language-images.json"

EXPECTED_KINDS = {
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


def test_cross_language_registry_loads_all_container_goblins() -> None:
    registry = GoblinRegistry.from_path(REGISTRY)

    assert {definition.kind for definition in registry.list()} == EXPECTED_KINDS
    for kind in EXPECTED_KINDS:
        definition, entrypoint = registry.resolve(kind)
        assert definition.module == "examples.goblins.container_only"
        assert callable(entrypoint)


def test_cross_language_image_map_covers_every_registered_goblin() -> None:
    registry = GoblinRegistry.from_path(REGISTRY)
    worker_map = WorkerImageMap.from_path(IMAGES)

    for definition in registry.list():
        worker = worker_map.get(definition.kind)
        context = worker_map.resolved_context(worker)
        assert context.is_dir()
        assert (context / worker.dockerfile).is_file()


def test_cross_language_goblins_are_visible_through_api(tmp_path: Path) -> None:
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
    assert set(goblins) == EXPECTED_KINDS
    assert all(item["worker_mapped"] for item in goblins.values())
