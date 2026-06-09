"""Tests for the supported root package boundary used by adopting projects."""

from __future__ import annotations

import goblin_king


def test_root_exports_adoption_primitives() -> None:
    """Verify host projects can import stable helpers from the package root."""
    expected = {
        "ApiSettings",
        "ENTRY_POINT_GROUP",
        "GoblinContext",
        "GoblinDefinition",
        "GoblinRegistry",
        "GoblinResult",
        "ProjectSettings",
        "Scheduler",
        "SQLiteStore",
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
