"""Internal loader and output helpers for the Goblin King Typer CLI."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

import typer

from goblin_king.jsonio import pretty_json, read_json_file
from goblin_king.project import ProjectSettings, ProjectSettingsError
from goblin_king.registry import GoblinRegistry, RegistryError
from goblin_king.resource_policies import ResourcePolicyError, ResourcePolicySet
from goblin_king.validation import WorkerValidationResult
from goblin_king.workers import WorkerConfigError, WorkerImageMap

RuntimeOption = Literal["docker", "kubernetes", "in-process"]
DEFAULT_IMAGES_PATH = Path("goblin-images.json")
DEFAULT_REDIS_URL = "redis://localhost:6379/0"
DEFAULT_PROJECT_PATH = Path("goblin-king-project.json")
DEFAULT_RESOURCE_POLICIES_PATH = Path("goblin-resource-policies.json")


def print_validation_results(
    results: list[WorkerValidationResult],
    *,
    json_output: bool,
) -> None:
    """Print validation results and exit nonzero when any contract check fails."""
    if json_output:
        typer.echo(pretty_json([result.model_dump(mode="json") for result in results]))
    else:
        for result in results:
            status = "ok" if result.ok else "failed"
            detail = result.error or ",".join(result.checks)
            typer.echo(f"{result.kind}\t{status}\t{result.result_status or '-'}\t{detail}")
    if any(not result.ok for result in results):
        raise typer.Exit(1)


def load_registry(path: Path) -> GoblinRegistry:
    """Load a registry for CLI commands and translate registry errors into process exits."""
    try:
        return GoblinRegistry.from_path(path)
    except RegistryError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from error


def load_workers(path: Path) -> WorkerImageMap:
    """Load worker image settings and translate errors into CLI exits."""
    try:
        return WorkerImageMap.from_path(path)
    except WorkerConfigError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from error


def load_project_settings(path: Path) -> ProjectSettings:
    """Load project settings and translate errors into CLI exits."""
    try:
        return ProjectSettings.from_path(path)
    except ProjectSettingsError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from error


def load_project_default_resources(path: Path) -> dict:
    """Return raw project defaults.resources for visibility-only CLI output."""
    try:
        payload = read_json_file(path)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    defaults = payload.get("defaults") if isinstance(payload, dict) else None
    if not isinstance(defaults, dict):
        return {}
    resources = defaults.get("resources")
    return resources if isinstance(resources, dict) else {}


def load_project_registry(path: Path) -> GoblinRegistry:
    """Load all goblins described by project settings."""
    settings = load_project_settings(path)
    try:
        return GoblinRegistry.from_project_sources(
            settings.registries,
            include_entry_points=settings.entry_points,
            definitions=settings.registry_definitions(),
        )
    except RegistryError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from error


def load_scheduler_discovery(
    registry_path: Path,
    images_path: Path,
    project_path: Path | None,
    runtime: RuntimeOption,
) -> tuple[GoblinRegistry, WorkerImageMap | None]:
    """Load scheduler registry and worker map from either direct paths or project settings."""
    if project_path is not None:
        settings = load_project_settings(project_path)
        registry = load_project_registry(project_path)
        workers = load_project_workers(settings) if runtime in {"docker", "kubernetes"} else None
        return registry, workers
    registry = load_registry(registry_path)
    workers = load_workers(images_path) if runtime in {"docker", "kubernetes"} else None
    return registry, workers


def load_resource_policies(
    path: Path | None,
    *,
    project: Path | None = None,
) -> ResourcePolicySet | None:
    """Load optional resource policies; missing default files mean enforcement is off."""
    project_settings = load_project_settings(project) if project is not None else None
    if path is None:
        return project_settings.resource_policy_set(None) if project_settings else None
    if not path.exists() and path == DEFAULT_RESOURCE_POLICIES_PATH:
        return project_settings.resource_policy_set(None) if project_settings else None
    try:
        policies = ResourcePolicySet.from_path(path)
    except ResourcePolicyError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from error
    return project_settings.resource_policy_set(policies) if project_settings else policies


def load_project_workers(settings: ProjectSettings) -> WorkerImageMap:
    """Load worker images plus inline project-config worker definitions."""
    try:
        return WorkerImageMap.from_path_and_definitions(
            settings.images,
            settings.worker_definitions(),
        )
    except WorkerConfigError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from error


def load_input(path: Path) -> dict:
    """Load one JSON object from disk for a goblin invocation."""
    try:
        payload = read_json_file(path)
    except FileNotFoundError as error:
        typer.echo(f"input not found: {path}", err=True)
        raise typer.Exit(1) from error
    except json.JSONDecodeError as error:
        typer.echo(f"input is not valid JSON: {path}", err=True)
        raise typer.Exit(1) from error
    if not isinstance(payload, dict):
        typer.echo("input JSON must be an object", err=True)
        raise typer.Exit(1)
    return payload
