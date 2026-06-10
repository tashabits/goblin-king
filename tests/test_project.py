"""Tests for Goblin King project integration settings."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from goblin_king.project import ProjectSettings, ProjectSettingsError
from goblin_king.registry import GoblinRegistry
from goblin_king.workers import WorkerImageMap


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


def test_project_settings_load_inline_goblin_config(tmp_path: Path) -> None:
    """Verify versioned GoblinProject config can define container-only goblins."""
    project_path = tmp_path / "project" / "goblin-king-project.json"
    project_path.parent.mkdir()
    (project_path.parent / "schemas").mkdir()
    project_path.write_text(
        json.dumps(
            {
                "apiVersion": "goblin-king/v1alpha1",
                "kind": "GoblinProject",
                "registries": [],
                "entry_points": False,
                "images": "images.json",
                "api_settings": "api.json",
                "goblins": {
                    "project.inline.hello": {
                        "displayName": "Project Inline Hello",
                        "description": "Project-owned hello goblin",
                        "image": "project-inline-hello:local",
                        "context": "workers/hello",
                        "inputSchema": "schemas/hello.schema.json",
                        "resources": {"timeout_seconds": 30},
                        "artifacts": {"enabled": False},
                        "labels": {"owner": "project"},
                        "tags": ["demo"],
                        "env": {"MODE": "demo"},
                        "secretRefs": ["demo-secret"],
                        "schedule": {"cron": "* * * * *"},
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    settings = ProjectSettings.from_path(project_path)
    definitions = settings.registry_definitions()
    workers = settings.worker_definitions()

    assert definitions[0].kind == "project.inline.hello"
    assert definitions[0].module == "goblin_king.container_only"
    assert definitions[0].metadata["description"] == "Project-owned hello goblin"
    assert definitions[0].metadata["input_schema"].endswith("schemas\\hello.schema.json")
    assert workers["project.inline.hello"].image == "project-inline-hello:local"
    assert workers["project.inline.hello"].context == (
        project_path.parent / "workers" / "hello"
    ).resolve()


def test_project_settings_reject_invalid_version_and_secret_values(tmp_path: Path) -> None:
    """Verify invalid project config versions and inline secret values fail clearly."""
    project_path = tmp_path / "goblin-king-project.json"
    project_path.write_text(
        json.dumps(
            {
                "apiVersion": "goblin-king/v9",
                "kind": "GoblinProject",
                "goblins": {},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ProjectSettingsError, match="apiVersion"):
        ProjectSettings.from_path(project_path)

    project_path.write_text(
        json.dumps(
            {
                "apiVersion": "goblin-king/v1alpha1",
                "kind": "GoblinProject",
                "goblins": {
                    "project.bad": {
                        "image": "bad:local",
                        "secretRefs": ["TOKEN=not-allowed"],
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ProjectSettingsError, match="secretRefs"):
        ProjectSettings.from_path(project_path)


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


def test_project_registry_and_worker_map_include_inline_goblins() -> None:
    """Verify the adopting-project fixture exposes registry and inline config goblins."""
    settings = ProjectSettings.from_path("examples/adopting-project/goblin-king-project.json")
    registry = GoblinRegistry.from_project_sources(
        settings.registries,
        include_entry_points=settings.entry_points,
        definitions=settings.registry_definitions(),
    )
    workers = WorkerImageMap.from_path_and_definitions(
        settings.images,
        settings.worker_definitions(),
    )

    kinds = [definition.kind for definition in registry.list()]
    assert "project.maintenance.hello" in kinds
    assert "project.inline.hello" in kinds
    assert workers.get("project.inline.hello").image == "project-inline-hello:local"
