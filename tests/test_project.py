"""Tests for Goblin King project integration settings."""

from __future__ import annotations

import json
from pathlib import Path

from goblin_king.project import ProjectSettings
from goblin_king.registry import GoblinRegistry


def test_project_settings_resolve_paths_relative_to_file(tmp_path: Path) -> None:
    """Verify project integration paths resolve from the settings file location."""
    project_path = tmp_path / "project" / "goblin-king-project.json"
    project_path.parent.mkdir()
    project_path.write_text(
        json.dumps(
            {
                "registries": ["one.json", "nested/two.json"],
                "entry_points": False,
                "images": "images.json",
                "api_settings": "api.json",
            }
        ),
        encoding="utf-8",
    )

    settings = ProjectSettings.from_path(project_path)

    assert settings.registries[0] == (project_path.parent / "one.json").resolve()
    assert settings.registries[1] == (project_path.parent / "nested" / "two.json").resolve()
    assert settings.images == (project_path.parent / "images.json").resolve()


def test_registry_merges_multiple_files(tmp_path: Path) -> None:
    """Verify multiple JSON registry files merge into one registry."""
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    first.write_text(
        '{"goblins":[{"kind":"one.echo","display_name":"One","module":"examples.goblins.echo"}]}',
        encoding="utf-8",
    )
    second.write_text(
        '{"goblins":[{"kind":"two.echo","display_name":"Two","module":"examples.goblins.echo"}]}',
        encoding="utf-8",
    )

    registry = GoblinRegistry.from_paths([first, second])

    assert [definition.kind for definition in registry.list()] == ["one.echo", "two.echo"]
