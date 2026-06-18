"""Tests for the supported root package boundary used by adopting projects."""

from __future__ import annotations

import json
import subprocess
import sys

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
        "GoblinKingNotebookClient",
        "GoblinRegistry",
        "GoblinResult",
        "PROJECT_CONFIG_API_VERSION",
        "PROJECT_CONFIG_KIND",
        "ProjectSettings",
        "NotebookASGIService",
        "NotebookFunctionGoblin",
        "NotebookGoblinRecord",
        "NotebookServiceRecord",
        "REGISTRY_SCHEMA_VERSION",
        "WORKER_HEARTBEAT_CONTRACT_VERSION",
        "WORKER_IMAGE_MAP_SCHEMA_VERSION",
        "WORKER_RESULT_CONTRACT_VERSION",
        "WorkerImageMap",
        "init_package",
    }

    assert expected.issubset(set(goblin_king.__all__))
    for name in expected:
        assert getattr(goblin_king, name)


def test_root_keeps_lazy_compatibility_exports() -> None:
    """Verify older root imports still work without eager runtime imports."""
    expected = {"Scheduler", "SQLiteStore", "create_app"}

    assert expected.issubset(set(goblin_king.__all__))
    assert goblin_king.Scheduler
    assert goblin_king.SQLiteStore
    assert goblin_king.create_app


def test_root_import_does_not_load_heavy_runtime_modules() -> None:
    """Verify package import stays lightweight for adopting projects."""
    script = """
import json
import sys
import goblin_king
print(json.dumps({
    name: name in sys.modules
    for name in [
        "goblin_king.api",
        "goblin_king.scheduler",
        "goblin_king.store",
        "goblin_king.runtime",
        "goblin_king.cli",
    ]
}))
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
    )
    loaded = json.loads(completed.stdout)

    assert loaded == {
        "goblin_king.api": False,
        "goblin_king.scheduler": False,
        "goblin_king.store": False,
        "goblin_king.runtime": False,
        "goblin_king.cli": False,
    }


def test_legacy_root_exports_are_loaded_on_demand() -> None:
    """Verify compatibility shims import heavy modules only when requested."""
    script = """
import json
import sys
import goblin_king
before = "goblin_king.api" in sys.modules
_ = goblin_king.create_app
after = "goblin_king.api" in sys.modules
print(json.dumps({"before": before, "after": after}))
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(completed.stdout) == {"before": False, "after": True}


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
