"""Tests for the supported root package boundary used by adopting projects."""

from __future__ import annotations

import goblin_king


def test_root_exports_adoption_primitives() -> None:
    """Verify host projects can import stable helpers from the package root."""
    expected = {
        "ApiSettings",
        "API_SETTINGS_SCHEMA_VERSION",
        "ENTRY_POINT_GROUP",
        "GOBLIN_CONTAINER_CONTRACT_VERSION",
        "GoblinContext",
        "GoblinDefinition",
        "GoblinRegistry",
        "GoblinResult",
        "PROJECT_CONFIG_API_VERSION",
        "PROJECT_CONFIG_KIND",
        "ProjectSettings",
        "REGISTRY_SCHEMA_VERSION",
        "Scheduler",
        "SQLiteStore",
        "WORKER_HEARTBEAT_CONTRACT_VERSION",
        "WORKER_IMAGE_MAP_SCHEMA_VERSION",
        "WORKER_RESULT_CONTRACT_VERSION",
        "WorkerImageMap",
        "create_app",
        "init_package",
    }

    assert expected.issubset(set(goblin_king.__all__))
    for name in expected:
        assert getattr(goblin_king, name)


def test_generated_goblin_style_imports_from_root() -> None:
    """Verify generated goblin packages do not need internal imports."""
    definition = goblin_king.GoblinDefinition(
        kind="project.hello",
        display_name="Project Hello",
        module="project_goblins.hello",
    )
    result = goblin_king.GoblinResult.ok(data={"message": "Hello"})

    assert definition.kind == "project.hello"
    assert result.status == "success"


def test_root_exports_v1alpha1_contract_versions() -> None:
    """Verify adopting projects can inspect supported alpha contract versions."""
    assert goblin_king.GOBLIN_CONTAINER_CONTRACT_VERSION == "goblin-king/v1alpha1"
    assert goblin_king.API_SETTINGS_SCHEMA_VERSION == "goblin-king/v1alpha1"
    assert goblin_king.PROJECT_CONFIG_API_VERSION == "goblin-king/v1alpha1"
    assert goblin_king.PROJECT_CONFIG_KIND == "GoblinProject"
    assert goblin_king.REGISTRY_SCHEMA_VERSION == "goblin-king/v1alpha1"
    assert goblin_king.WORKER_IMAGE_MAP_SCHEMA_VERSION == "goblin-king/v1alpha1"
    assert goblin_king.WORKER_RESULT_CONTRACT_VERSION == "goblin-king/v1alpha1"
    assert goblin_king.WORKER_HEARTBEAT_CONTRACT_VERSION == "goblin-king/v1alpha1"
