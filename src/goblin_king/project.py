"""Project integration settings for reusable Goblin King adoption."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from goblin_king.contracts import GoblinDefinition
from goblin_king.jsonio import read_json_file
from goblin_king.resource_policies import ResourcePolicy, ResourcePolicyError, ResourcePolicySet
from goblin_king.versions import PROJECT_CONFIG_API_VERSION, PROJECT_CONFIG_KIND
from goblin_king.workers import WorkerImageDefinition

_LABEL_NAME_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,61}[A-Za-z0-9])?$")
_DNS_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


class ProjectSettingsError(ValueError):
    """Raised when project integration settings cannot be loaded."""


class ProjectPlacementSpec(BaseModel):
    """Scheduling placement intent expressed as validated Kubernetes-style labels."""

    model_config = ConfigDict(extra="forbid")

    required: dict[str, str] = Field(default_factory=dict)
    preferred: dict[str, str] = Field(default_factory=dict)

    @field_validator("required", "preferred", mode="before")
    @classmethod
    def validate_label_map(cls, value: Any) -> dict[str, str]:
        """Accept only label selector maps, not raw pod spec fragments."""
        if not isinstance(value, dict):
            raise ValueError("placement labels must be an object")
        for key, label_value in value.items():
            if not isinstance(key, str):
                raise ValueError("placement label keys must be strings")
            if not isinstance(label_value, str):
                raise ValueError("placement label values must be strings")
            _validate_label_key(key)
            _validate_label_value(label_value)
        return value

    def normalized(self) -> dict[str, dict[str, str]]:
        """Return a stable JSON-ready placement object for definition metadata."""
        return {
            "required": dict(sorted(self.required.items())),
            "preferred": dict(sorted(self.preferred.items())),
        }


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
    placement: ProjectPlacementSpec = Field(default_factory=ProjectPlacementSpec)
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


class ProjectServiceSpec(BaseModel):
    """Describe a project-owned long-running HTTP service workload."""

    model_config = ConfigDict(populate_by_name=True)

    image: str = Field(min_length=1)
    description: str | None = None
    display_name: str | None = Field(default=None, alias="displayName")
    context: Path = Path(".")
    dockerfile: str = "Dockerfile"
    base_url: str | None = Field(default=None, alias="baseUrl")
    port: int | None = Field(default=None, gt=0, le=65535)
    probe_path: str = Field(default="/hello", alias="probePath", min_length=1)
    resources: dict[str, Any] = Field(default_factory=dict)
    labels: dict[str, str] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    secret_refs: list[str] = Field(default_factory=list, alias="secretRefs")

    @field_validator("probe_path")
    @classmethod
    def validate_probe_path(cls, value: str) -> str:
        """Keep service probes scoped to an absolute path on the registered base URL."""
        if not value.startswith("/"):
            raise ValueError("probePath must start with /")
        return value

    @field_validator("secret_refs")
    @classmethod
    def validate_secret_refs(cls, value: list[str]) -> list[str]:
        """Keep secret references symbolic; secret values never live in project config."""
        for item in value:
            if "=" in item:
                raise ValueError("secretRefs must name secrets, not contain secret values")
        return value

    @model_validator(mode="after")
    def validate_endpoint(self) -> ProjectServiceSpec:
        """Require enough endpoint metadata to register or expose the service."""
        if self.base_url is None and self.port is None:
            raise ValueError("service workloads must set either baseUrl or port")
        return self


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
    services: dict[str, ProjectServiceSpec] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_unique_workload_kinds(self) -> ProjectSettings:
        """Prevent one kind from being both a job goblin and a service workload."""
        duplicates = sorted(set(self.goblins).intersection(self.services))
        if duplicates:
            raise ValueError(
                "project service kind also defined as goblin: " + ", ".join(duplicates)
            )
        return self

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
        services = {}
        for kind, spec in self.services.items():
            resources = _deep_merge(defaults, spec.resources)
            _validate_resource_policy(kind, resources, resource_ceilings)
            services[kind] = spec.model_copy(update={"resources": resources})
        return self.model_copy(update={"goblins": goblins, "services": services})

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
        services = {
            kind: spec.model_copy(update={"context": _resolve(root, spec.context)})
            for kind, spec in self.services.items()
        }
        return self.model_copy(
            update={
                "registries": [_resolve(root, path) for path in self.registries],
                "images": _resolve(root, self.images),
                "api_settings": _resolve(root, self.api_settings),
                "goblins": goblins,
                "services": services,
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
        for kind, spec in self.services.items():
            definitions.append(
                GoblinDefinition(
                    kind=kind,
                    display_name=spec.display_name or _display_name(kind),
                    module="goblin_king.container_only",
                    metadata=_project_service_metadata(spec),
                )
            )
        return definitions

    def worker_definitions(self) -> dict[str, WorkerImageDefinition]:
        """Convert inline project goblins into worker image definitions."""
        definitions = {
            kind: WorkerImageDefinition(
                context=spec.context,
                dockerfile=spec.dockerfile,
                image=spec.image,
            )
            for kind, spec in self.goblins.items()
        }
        definitions.update(
            {
                kind: WorkerImageDefinition(
                    context=spec.context,
                    dockerfile=spec.dockerfile,
                    image=spec.image,
                )
                for kind, spec in self.services.items()
            }
        )
        return definitions

    def resource_policy_set(
        self,
        operator_policies: ResourcePolicySet | None = None,
    ) -> ResourcePolicySet | None:
        """Return operator policies layered with project defaults and goblin resources."""
        project_defaults = self.defaults.resources
        project_goblins = {
            kind: spec.resources for kind, spec in self.goblins.items() if spec.resources
        }
        project_services = {
            kind: spec.resources for kind, spec in self.services.items() if spec.resources
        }
        project_workloads = {**project_goblins, **project_services}
        if not project_defaults and not project_workloads and operator_policies is None:
            return None

        base = operator_policies or ResourcePolicySet()
        defaults = ResourcePolicy.model_validate(
            _deep_merge(base.defaults.compact(), project_defaults)
        )
        goblins = dict(base.goblins)
        for kind, resources in project_workloads.items():
            base_resources = goblins.get(kind, ResourcePolicy()).compact()
            goblins[kind] = ResourcePolicy.model_validate(
                _deep_merge(base_resources, resources)
            )
        merged = ResourcePolicySet(
            version=base.version,
            defaults=defaults,
            goblins=goblins,
            ceilings=base.ceilings,
        )
        merged.validate_within_ceilings("<project defaults>", defaults)
        for kind in project_workloads:
            merged.validate_within_ceilings(kind, merged.effective_for(kind))
        return merged


def _resolve(root: Path, path: Path) -> Path:
    """Resolve one project path without requiring it to exist."""
    if path.is_absolute():
        return path
    return (root / path).resolve()


def _display_name(kind: str) -> str:
    """Create a readable display name from a project goblin kind."""
    return " ".join(part.capitalize() for part in kind.replace("-", ".").split("."))


def _validate_label_key(value: str) -> None:
    """Validate Kubernetes label key syntax for project placement selectors."""
    if not value:
        raise ValueError("placement label keys must not be empty")
    if "/" in value:
        prefix, name = value.rsplit("/", 1)
        if not prefix or not name or "/" in prefix:
            raise ValueError(f"invalid placement label key: {value!r}")
        if len(prefix) > 253:
            raise ValueError(f"placement label key prefix is too long: {value!r}")
        prefix_parts = prefix.split(".")
        if any(not part or not _DNS_LABEL_RE.match(part) for part in prefix_parts):
            raise ValueError(f"invalid placement label key prefix: {value!r}")
    else:
        name = value
    if len(name) > 63 or not _LABEL_NAME_RE.match(name):
        raise ValueError(f"invalid placement label key name: {value!r}")


def _validate_label_value(value: str) -> None:
    """Validate Kubernetes label value syntax while rejecting empty values."""
    if not value:
        raise ValueError("placement label values must not be empty")
    if len(value) > 63 or not _LABEL_NAME_RE.match(value):
        raise ValueError(f"invalid placement label value: {value!r}")


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
        "placement": spec.placement.normalized(),
        "env": spec.env,
        "secret_refs": spec.secret_refs,
        "schedule": spec.schedule,
    }


def _project_service_metadata(spec: ProjectServiceSpec) -> dict[str, Any]:
    """Return metadata for project-config service workload discovery."""
    return {
        "source": "project-config",
        "workload_type": "service",
        "description": spec.description,
        "base_url": spec.base_url,
        "port": spec.port,
        "probe_path": spec.probe_path,
        "resources": spec.resources,
        "labels": spec.labels,
        "tags": spec.tags,
        "env": spec.env,
        "secret_refs": spec.secret_refs,
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
