"""Tests for Goblin King project integration settings."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from goblin_king.project import ProjectSettings, ProjectSettingsError
from goblin_king.registry import GoblinRegistry
from goblin_king.resource_policies import ResourcePolicyError, ResourcePolicySet
from goblin_king.versions import PROJECT_CONFIG_API_VERSION, PROJECT_CONFIG_KIND
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
                "apiVersion": PROJECT_CONFIG_API_VERSION,
                "kind": PROJECT_CONFIG_KIND,
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


def test_project_settings_apply_default_resources_to_inline_goblins(
    tmp_path: Path,
) -> None:
    """Verify defaults.resources merge into each inline goblin resource policy."""
    project_path = tmp_path / "goblin-king-project.json"
    project_path.write_text(
        json.dumps(
            {
                "apiVersion": PROJECT_CONFIG_API_VERSION,
                "kind": PROJECT_CONFIG_KIND,
                "defaults": {
                    "resources": {
                        "timeout_seconds": 30,
                        "cpu": {"request": "100m", "limit": "500m"},
                        "memory": {"request": "64Mi", "limit": "256Mi"},
                    }
                },
                "goblins": {
                    "project.defaulted": {
                        "image": "defaulted:local",
                    },
                    "project.override": {
                        "image": "override:local",
                        "resources": {
                            "timeout_seconds": 60,
                            "memory": {"limit": "512Mi"},
                        },
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    settings = ProjectSettings.from_path(project_path)
    definitions = {
        definition.kind: definition for definition in settings.registry_definitions()
    }

    assert definitions["project.defaulted"].metadata["resources"] == {
        "timeout_seconds": 30,
        "cpu": {"request": "100m", "limit": "500m"},
        "memory": {"request": "64Mi", "limit": "256Mi"},
    }
    assert definitions["project.override"].metadata["resources"] == {
        "timeout_seconds": 60,
        "cpu": {"request": "100m", "limit": "500m"},
        "memory": {"request": "64Mi", "limit": "512Mi"},
    }
    assert settings.defaults.resources["memory"]["limit"] == "256Mi"


def test_project_settings_reject_invalid_default_resources(tmp_path: Path) -> None:
    """Verify project default resources use existing runtime policy validation."""
    project_path = tmp_path / "goblin-king-project.json"
    project_path.write_text(
        json.dumps(
            {
                "apiVersion": PROJECT_CONFIG_API_VERSION,
                "kind": PROJECT_CONFIG_KIND,
                "defaults": {"resources": {"timeout_seconds": 0}},
                "goblins": {"project.bad": {"image": "bad:local"}},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ProjectSettingsError, match="timeout_seconds"):
        ProjectSettings.from_path(project_path)


def test_project_settings_reject_resources_above_discovered_ceilings(
    tmp_path: Path,
) -> None:
    """Verify sibling resource-policy ceilings apply to project default resources."""
    (tmp_path / "goblin-resource-policies.json").write_text(
        json.dumps(
            {
                "version": 1,
                "ceilings": {
                    "timeout_seconds": 60,
                    "memory": {"limit": "256Mi"},
                },
            }
        ),
        encoding="utf-8",
    )
    project_path = tmp_path / "goblin-king-project.json"
    project_path.write_text(
        json.dumps(
            {
                "apiVersion": PROJECT_CONFIG_API_VERSION,
                "kind": PROJECT_CONFIG_KIND,
                "defaults": {
                    "resources": {
                        "timeout_seconds": 30,
                        "memory": {"limit": "512Mi"},
                    }
                },
                "goblins": {"project.bad": {"image": "bad:local"}},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ProjectSettingsError, match="memory.limit"):
        ProjectSettings.from_path(project_path)


def test_project_resource_policy_set_layers_goblin_resources() -> None:
    """Verify runtime policy resolution layers global, project, and goblin resources."""
    settings = ProjectSettings(
        defaults={
            "resources": {
                "timeout_seconds": 30,
                "cpu": {"request": "100m"},
                "memory": {"limit": "256Mi"},
                "filesystem": {"artifact_max_files": 3},
            }
        },
        goblins={
            "project.inline": {
                "image": "inline:local",
                "resources": {
                    "timeout_seconds": 45,
                    "memory": {"request": "64Mi"},
                    "filesystem": {"artifact_max_bytes": 1024},
                },
            }
        },
    ).with_effective_resource_defaults()
    operator_policies = ResourcePolicySet.model_validate(
        {
            "version": 1,
            "defaults": {
                "max_retries": 1,
                "cpu": {"limit": "1"},
                "memory": {"request": "32Mi"},
                "logs": {"max_bytes": 2048},
            },
            "goblins": {
                "project.inline": {
                    "filesystem": {"read_only_root": True},
                }
            },
            "ceilings": {
                "timeout_seconds": 60,
                "memory": {"limit": "512Mi"},
            },
        }
    )

    policy_set = settings.resource_policy_set(operator_policies)
    assert policy_set is not None
    policy = policy_set.effective_for("project.inline")

    assert policy.timeout_seconds == 45
    assert policy.max_retries == 1
    assert policy.cpu.request == "100m"
    assert policy.cpu.limit == "1"
    assert policy.memory.request == "64Mi"
    assert policy.memory.limit == "256Mi"
    assert policy.filesystem.read_only_root is True
    assert policy.filesystem.artifact_max_files == 3
    assert policy.filesystem.artifact_max_bytes == 1024
    assert policy.logs.max_bytes == 2048


def test_project_resource_policy_set_rejects_goblin_resources_above_ceilings() -> None:
    """Verify inline per-goblin resources are checked against runtime ceilings."""
    settings = ProjectSettings(
        goblins={
            "project.too-large": {
                "image": "too-large:local",
                "resources": {"memory": {"limit": "1Gi"}},
            }
        },
    ).with_effective_resource_defaults()
    operator_policies = ResourcePolicySet.model_validate(
        {
            "version": 1,
            "ceilings": {"memory": {"limit": "512Mi"}},
        }
    )

    with pytest.raises(ResourcePolicyError, match="memory.limit"):
        settings.resource_policy_set(operator_policies)


def test_project_settings_reject_unknown_resource_fields(tmp_path: Path) -> None:
    """Verify project resource policy metadata rejects fields outside the policy model."""
    project_path = tmp_path / "goblin-king-project.json"
    project_path.write_text(
        json.dumps(
            {
                "apiVersion": PROJECT_CONFIG_API_VERSION,
                "kind": PROJECT_CONFIG_KIND,
                "defaults": {"resources": {"memory": {"reservation": "64Mi"}}},
                "goblins": {"project.bad": {"image": "bad:local"}},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ProjectSettingsError, match="reservation"):
        ProjectSettings.from_path(project_path)


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
                "apiVersion": PROJECT_CONFIG_API_VERSION,
                "kind": PROJECT_CONFIG_KIND,
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
