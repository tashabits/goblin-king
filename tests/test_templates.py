"""Tests for reusable goblin package template generation."""

from __future__ import annotations

import json
from pathlib import Path

from goblin_king.project import ProjectSettings
from goblin_king.templates import init_package, init_project


def test_init_package_creates_reusable_package_skeleton(tmp_path: Path) -> None:
    """Verify package generation writes Python, registry, image, test, and worker files."""
    target = init_package(
        tmp_path / "generated",
        kind="sample.echo",
        package_name="sample_echo",
        image="sample-echo:local",
    )

    assert (target / "pyproject.toml").exists()
    assert (target / "goblin-king-project.json").exists()
    assert (target / "goblin-king-api.json").exists()
    assert (target / "sample_echo" / "goblin.py").exists()
    assert (target / "sample_echo" / "long_service.py").exists()
    assert (target / "tests" / "test_goblin.py").exists()
    assert (target / "workers" / "sample.echo" / "Dockerfile").exists()
    assert (target / "workers" / "sample.echo.long-service" / "Dockerfile").exists()
    assert (target / "workers" / "sample.echo.long-service" / "service.py").exists()
    registry = json.loads((target / "goblins.json").read_text(encoding="utf-8"))
    images = json.loads((target / "goblin-images.json").read_text(encoding="utf-8"))
    assert registry["goblins"][0]["kind"] == "sample.echo"
    assert registry["goblins"][1]["kind"] == "sample.echo.long-service"
    assert images["workers"]["sample.echo"]["image"] == "sample-echo:local"
    assert images["workers"]["sample.echo.long-service"]["image"] == "sample-echo-long:local"
    assert "goblin_king.goblins" in (target / "pyproject.toml").read_text(encoding="utf-8")
    assert "sample_echo_long_service" in (target / "pyproject.toml").read_text(
        encoding="utf-8"
    )


def test_init_package_can_skip_long_service_worker(tmp_path: Path) -> None:
    """Verify package generation can omit the optional service worker."""
    target = init_package(
        tmp_path / "generated",
        kind="sample.echo",
        package_name="sample_echo",
        image="sample-echo:local",
        include_long_service=False,
    )

    registry = json.loads((target / "goblins.json").read_text(encoding="utf-8"))
    images = json.loads((target / "goblin-images.json").read_text(encoding="utf-8"))
    assert len(registry["goblins"]) == 1
    assert list(images["workers"]) == ["sample.echo"]
    assert not (target / "workers" / "sample.echo.long-service").exists()
    assert "long_service" not in (target / "pyproject.toml").read_text(encoding="utf-8")


def test_init_project_creates_adopter_project_template(tmp_path: Path) -> None:
    """Verify the adopter project template includes config, workers, inputs, and docs."""
    target = init_project(tmp_path / "project", prefix="acme")

    assert (target / "goblin-king-project.json").exists()
    assert (target / "goblin-images.json").exists()
    assert (target / "inputs" / "hello.json").exists()
    assert (target / "inputs" / "artifact.json").exists()
    assert (target / "schemas" / "hello.input.schema.json").exists()
    assert (target / "workers" / "acme.hello" / "Dockerfile").exists()
    assert (target / "workers" / "acme.artifact" / "worker.py").exists()
    readme = (target / "README.md").read_text(encoding="utf-8")
    assert "project goblins list" in readme
    assert "workers validate" in readme
    assert "workers validation-status" in readme
    assert "jobs submit acme.hello" in readme
    assert "runs show <artifact-run-id> --with-job" in readme
    assert "scheduler run-once" in readme

    settings = ProjectSettings.from_path(target / "goblin-king-project.json")
    definitions = settings.registry_definitions()
    workers = settings.worker_definitions()

    assert [definition.kind for definition in definitions] == [
        "acme.hello",
        "acme.artifact",
    ]
    assert workers["acme.hello"].image == "acme-hello:local"
    assert workers["acme.artifact"].context == target / "workers" / "acme.artifact"
