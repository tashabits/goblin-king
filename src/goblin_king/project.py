"""Project integration settings for reusable Goblin King adoption."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from goblin_king.contracts import GoblinDefinition
from goblin_king.jsonio import read_json_file
from goblin_king.resource_policies import ResourcePolicy, ResourcePolicyError, ResourcePolicySet
from goblin_king.versions import PROJECT_CONFIG_API_VERSION, PROJECT_CONFIG_KIND
from goblin_king.workers import WorkerImageDefinition


class ProjectSettingsError(ValueError):
    """Raised when project integration settings cannot be loaded."""


class ProjectDefaults(BaseModel):
    """Project-level defaults applied to inline goblin definitions."""

    resources: dict[str, Any] = Field(default_factory=dict)


class ProjectGoblinSpec(BaseModel):
    """Describe a project-owned container goblin without Python worker imports."""

    model_config = ConfigDict(populate_by_name=True)

    image: str = Field(min_length=1)
    description: str | None = None
    display_name: str | None = Field(default=None, alias="displayName")
    context: Path = Path(".")
    dockerfile: str = "Dockerfile"
    input_schema: Path | None = Field(default=None, alias="inputSchema")
    resource_policy: str | dict[str, Any] | None = Field(
        default=None,
        alias="resourcePolicy",
    )
    resources: dict[str, Any] = Field(default_factory=dict)
    artifacts: dict[str, Any] = Field(default_factory=dict)
    labels: dict[str, str] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    secret_refs: list[str] = Field(default_factory=list, alias="secretRefs")
    schedule: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: int | None = Field(default=None, alias="timeoutSeconds", gt=0)
    max_retries: int | None = Field(default=None, alias="maxRetries", ge=0)

    @field_validator("secret_refs")
    @classmethod
    def validate_secret_refs(cls, value: list[str]) -> list[str]:
        """Keep secret references symbolic; secret values never live in project config."""
        for item in value:
            if "=" in item:
                raise ValueError("secretRefs must name secrets, not contain secret values")
        return value


class ProjectSettings(BaseModel):
    """Describe registry, worker image, and API settings for an adopting project."""

    model_config = ConfigDict(populate_by_name=True)

    api_version: str | None = Field(default=None, alias="apiVersion")
    kind: str | None = None
    registries: list[Path] = Field(default_factory=lambda: [Path("examples/goblins.json")])
    entry_points: bool = True
    images: Path = Path("goblin-images.json")
    api_settings: Path = Path("goblin-king-api.json")
    defaults: ProjectDefaults = Field(default_factory=ProjectDefaults)
    goblins: dict[str, ProjectGoblinSpec] = Field(default_factory=dict)

    @field_validator("api_version")
    @classmethod
    def validate_api_version(cls, value: str | None) -> str | None:
        """Validate the optional project config API version."""
        if value is not None and value != PROJECT_CONFIG_API_VERSION:
            raise ValueError(f"apiVersion must be {PROJECT_CONFIG_API_VERSION}")
        return value

    @field_validator("kind")
    @classmethod
    def validate_kind(cls, value: str | None) -> str | None:
        """Validate the optional project config kind marker."""
        if value is not None and value != PROJECT_CONFIG_KIND:
            raise ValueError(f"kind must be {PROJECT_CONFIG_KIND}")
        return value

    @classmethod
    def from_path(cls, path: str | Path) -> ProjectSettings:
        """Load project settings from JSON and resolve paths relative to that file."""
        settings_path = Path(path)
        try:
            payload = read_json_file(settings_path)
        except FileNotFoundError as error:
            raise ProjectSettingsError(
                f"project settings file not found: {settings_path}"
            ) from error
        except json.JSONDecodeError as error:
            raise ProjectSettingsError(
                f"project settings file is not valid JSON: {settings_path}"
            ) from error
        try:
            settings = cls.model_validate(payload)
        except ValidationError as error:
            raise ProjectSettingsError(str(error)) from error
        root = settings_path.resolve().parent
        try:
            settings = settings.with_effective_resource_defaults(
                resource_ceilings=_discover_resource_ceilings(root)
            )
        except (ResourcePolicyError, ValueError) as error:
            raise ProjectSettingsError(str(error)) from error
        return settings.resolve_relative_to(root)

    def with_effective_resource_defaults(
        self,
        *,
        resource_ceilings: ResourcePolicy | None = None,
    ) -> ProjectSettings:
        """Merge defaults.resources into goblins and validate the effective policies."""
        defaults = self.defaults.resources
        _validate_resource_policy("defaults.resources", defaults, resource_ceilings)
        goblins = {}
        for kind, spec in self.goblins.items():
            resources = _deep_merge(defaults, spec.resources)
            _validate_resource_policy(kind, resources, resource_ceilings)
            goblins[kind] = spec.model_copy(update={"resources": resources})
        return self.model_copy(update={"goblins": goblins})

    def resolve_relative_to(self, root: Path) -> ProjectSettings:
        """Resolve path fields relative to the settings file directory."""
        goblins = {
            kind: spec.model_copy(
                update={
                    "context": _resolve(root, spec.context),
                    "input_schema": _resolve(root, spec.input_schema)
                    if spec.input_schema
                    else None,
                }
            )
            for kind, spec in self.goblins.items()
        }
        return self.model_copy(
            update={
                "registries": [_resolve(root, path) for path in self.registries],
                "images": _resolve(root, self.images),
                "api_settings": _resolve(root, self.api_settings),
                "goblins": goblins,
            }
        )

    def registry_definitions(self) -> list[GoblinDefinition]:
        """Convert inline project goblins into registry definitions."""
        definitions: list[GoblinDefinition] = []
        for kind, spec in self.goblins.items():
            metadata = _project_metadata(spec)
            definitions.append(
                GoblinDefinition(
                    kind=kind,
                    display_name=spec.display_name or _display_name(kind),
                    module="goblin_king.container_only",
                    timeout_seconds=spec.timeout_seconds,
                    max_retries=spec.max_retries,
                    metadata=metadata,
                )
            )
        return definitions

    def worker_definitions(self) -> dict[str, WorkerImageDefinition]:
        """Convert inline project goblins into worker image definitions."""
        return {
            kind: WorkerImageDefinition(
                context=spec.context,
                dockerfile=spec.dockerfile,
                image=spec.image,
            )
            for kind, spec in self.goblins.items()
        }

    def resource_policy_set(
        self,
        operator_policies: ResourcePolicySet | None = None,
    ) -> ResourcePolicySet | None:
        """Return operator policies with project defaults layered into defaults."""
        project_defaults = self.defaults.resources
        if not project_defaults and operator_policies is None:
            return None

        base = operator_policies or ResourcePolicySet()
        defaults = ResourcePolicy.model_validate(
            _deep_merge(base.defaults.compact(), project_defaults)
        )
        merged = ResourcePolicySet(
            version=base.version,
            defaults=defaults,
            goblins=base.goblins,
            ceilings=base.ceilings,
        )
        merged.validate_within_ceilings("<project defaults>", defaults)
        return merged


def _resolve(root: Path, path: Path) -> Path:
    """Resolve one project path without requiring it to exist."""
    if path.is_absolute():
        return path
    return (root / path).resolve()


def _display_name(kind: str) -> str:
    """Create a readable display name from a project goblin kind."""
    return " ".join(part.capitalize() for part in kind.replace("-", ".").split("."))


def _project_metadata(spec: ProjectGoblinSpec) -> dict[str, Any]:
    """Return metadata preserved for project-config documentation and future phases."""
    return {
        "source": "project-config",
        "description": spec.description,
        "input_schema": str(spec.input_schema) if spec.input_schema else None,
        "resource_policy": spec.resource_policy,
        "resources": spec.resources,
        "artifacts": spec.artifacts,
        "labels": spec.labels,
        "tags": spec.tags,
        "env": spec.env,
        "secret_refs": spec.secret_refs,
        "schedule": spec.schedule,
    }


def _validate_resource_policy(
    kind: str,
    resources: dict[str, Any],
    ceilings: ResourcePolicy | None,
) -> None:
    """Validate project resource metadata with the runtime resource policy model."""
    policy = ResourcePolicy.model_validate(resources)
    if ceilings is not None:
        ResourcePolicySet(ceilings=ceilings).validate_within_ceilings(kind, policy)


def _discover_resource_ceilings(root: Path) -> ResourcePolicy | None:
    """Load sibling resource-policy ceilings when an adopting project provides them."""
    policy_path = root / "goblin-resource-policies.json"
    if not policy_path.exists():
        return None
    return ResourcePolicySet.from_path(policy_path).ceilings


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Merge nested dictionaries without mutating project defaults or goblin overrides."""
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged
