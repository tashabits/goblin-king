from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

from goblin_king.contracts import GoblinResult
from goblin_king.project import ProjectSettings
from goblin_king.registry import GoblinRegistry
from goblin_king.resource_policies import ResourcePolicySet
from goblin_king.workers import WorkerImageMap

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "worker-backbone"
PROJECT = EXAMPLE / "goblin-king-project.json"

EXPECTED_KINDS = {
    "example.worker-backbone.artifact-manifest",
    "example.worker-backbone.catalog-service",
    "example.worker-backbone.local-rag",
    "example.worker-backbone.normalize-note",
}


def test_worker_backbone_project_config_loads() -> None:
    settings = ProjectSettings.from_path(PROJECT)
    registry = GoblinRegistry.from_project_sources(
        settings.registries,
        include_entry_points=settings.entry_points,
        definitions=settings.registry_definitions(),
    )

    assert settings.entry_points is False
    assert settings.images == (EXAMPLE / "goblin-images.json").resolve()
    assert settings.api_settings == (EXAMPLE / "goblin-king-api.json").resolve()
    assert settings.api_settings.is_file()
    assert settings.registries == [(EXAMPLE / "registries" / "worker-backbone.json").resolve()]
    assert set(settings.services) == {"example.worker-backbone.catalog-service"}
    assert {definition.kind for definition in registry.list()} == EXPECTED_KINDS

    rag_definition = registry.get("example.worker-backbone.local-rag")
    assert rag_definition.metadata["recipe"] == "rag-first-use-case"
    _assert_metadata_paths_exist(registry)


def test_worker_backbone_worker_images_cover_all_project_definitions() -> None:
    settings = ProjectSettings.from_path(PROJECT)
    registry = GoblinRegistry.from_project_sources(
        settings.registries,
        include_entry_points=settings.entry_points,
        definitions=settings.registry_definitions(),
    )
    worker_map = WorkerImageMap.from_path_and_definitions(
        settings.images,
        settings.worker_definitions(),
    )

    assert {kind for kind, _ in worker_map.items()} == EXPECTED_KINDS
    for definition in registry.list():
        worker = worker_map.get(definition.kind)
        context = worker_map.resolved_context(worker)
        assert context.is_dir(), f"{definition.kind} context is missing"
        assert (context / worker.dockerfile).is_file(), (
            f"{definition.kind} Dockerfile is missing"
        )
        assert worker.image.endswith(":local")


def test_worker_backbone_resource_policy_fixture_covers_rag_use_case() -> None:
    policies = ResourcePolicySet.from_path(EXAMPLE / "goblin-resource-policies.json")
    policy = policies.effective_for("example.worker-backbone.local-rag")

    assert policy.timeout_seconds == 60
    assert policy.network.mode == "none"
    assert policy.filesystem.read_only_root is True
    assert policy.logs.max_bytes == 8192


def test_worker_backbone_rag_fixture_is_deterministic_without_docker() -> None:
    worker = _load_module(EXAMPLE / "rag-first-use-case" / "workers" / "local-rag" / "worker.py")
    payload = json.loads(
        (EXAMPLE / "rag-first-use-case" / "inputs" / "query.input.json").read_text(
            encoding="utf-8"
        )
    )

    first = worker.build_result(payload)
    second = worker.build_result(payload)

    assert first == second
    result = GoblinResult.model_validate(first)
    assert result.status == "success"
    assert result.error is None
    assert result.artifacts == []
    assert result.handoff == []
    assert result.metrics == {
        "documents_scored": 4,
        "matches_returned": 2,
        "best_score": 7,
    }
    assert result.data["policy"] == {
        "model": "deterministic-lexical-fixture",
        "external_calls": 0,
    }
    assert result.data["matches"][0]["id"] == "local-rag"
    assert result.data["answer"].startswith("Local RAG Fixture:")


def _assert_metadata_paths_exist(registry: GoblinRegistry) -> None:
    for definition in registry.list():
        for key in ("input_schema", "sample_input", "fixture"):
            relative_path = definition.metadata.get(key)
            if relative_path:
                path = EXAMPLE / relative_path
                assert path.is_file(), f"{definition.kind} metadata {key} is missing"
                json.loads(path.read_text(encoding="utf-8"))


def _load_module(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location("worker_backbone_local_rag", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module
