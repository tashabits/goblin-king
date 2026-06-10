"""Tests for project-ready release, upgrade, and compatibility documentation."""

from __future__ import annotations

import json
from pathlib import Path

from goblin_king.project import ProjectSettings
from goblin_king.registry import GoblinRegistry
from goblin_king.versions import (
    API_SETTINGS_SCHEMA_VERSION,
    GOBLIN_CONTAINER_CONTRACT_VERSION,
    PROJECT_CONFIG_API_VERSION,
    REGISTRY_SCHEMA_VERSION,
    WORKER_HEARTBEAT_CONTRACT_VERSION,
    WORKER_IMAGE_MAP_SCHEMA_VERSION,
    WORKER_RESULT_CONTRACT_VERSION,
)
from goblin_king.workers import WorkerImageMap


def test_compatibility_matrix_matches_project_ready_baseline() -> None:
    """Verify the machine-readable matrix records the supported adoption contracts."""
    matrix = json.loads(Path("compatibility/goblin-king-compatibility.json").read_text())

    assert matrix["goblin_king_version"] == "0.1.0"
    assert matrix["goblin_contract_version"] == GOBLIN_CONTAINER_CONTRACT_VERSION
    assert matrix["registry_schema_version"] == REGISTRY_SCHEMA_VERSION
    assert matrix["worker_image_map_schema_version"] == WORKER_IMAGE_MAP_SCHEMA_VERSION
    assert matrix["worker_result_contract_version"] == WORKER_RESULT_CONTRACT_VERSION
    assert matrix["worker_heartbeat_contract_version"] == WORKER_HEARTBEAT_CONTRACT_VERSION
    assert matrix["api_settings_schema_version"] == API_SETTINGS_SCHEMA_VERSION
    assert matrix["project_settings_schema_version"] == PROJECT_CONFIG_API_VERSION


def test_project_ready_compatibility_fixture_discovers_and_maps_workers() -> None:
    """Verify the baseline host-project fixture remains usable after upgrades."""
    project_path = Path("examples/compatibility/project-ready-v0_1/goblin-king-project.json")
    settings = ProjectSettings.from_path(project_path)
    registry = GoblinRegistry.from_project_sources(
        settings.registries,
        include_entry_points=settings.entry_points,
    )
    workers = WorkerImageMap.from_path(settings.images)

    kinds = [definition.kind for definition in registry.list()]
    assert kinds == ["compat.hello"]
    worker = workers.get("compat.hello")
    assert workers.resolved_context(worker).joinpath(worker.dockerfile).exists()


def test_release_docs_are_linked_from_readme() -> None:
    """Verify project-ready release docs exist and are discoverable."""
    readme = Path("README.md").read_text(encoding="utf-8")
    required_docs = [
        "docs/FIRST_HOUR.md",
        "docs/RELEASE_CHECKLIST.md",
        "docs/COMPATIBILITY.md",
        "docs/UPGRADING.md",
        "docs/MIGRATION_GUIDE.md",
    ]

    for doc in required_docs:
        assert Path(doc).exists()
        assert doc in readme
