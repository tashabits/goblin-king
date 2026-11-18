"""Reusable delivery contracts owned by the pipeline control plane and source authors."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

EnvironmentName = Literal["test", "prod"]
_DNS_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
_REPOSITORY = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]{1,100}")
_IMAGE_PATH = re.compile(r"[a-z0-9]+(?:(?:[._]|__|-+)[a-z0-9]+)*")


class _PipelineModel(BaseModel):
    """Keep configuration explicit and immutable after validation."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


def _plain_text(value: str) -> str:
    """Reject empty, padded, or control-bearing identifiers and command arguments."""
    if (
        not value
        or value != value.strip()
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise ValueError("must be nonempty text without padding or control characters")
    return value


def _repository(value: str) -> str:
    """Normalize a provider repository key; URLs and credential-bearing forms are excluded."""
    if not _REPOSITORY.fullmatch(value):
        raise ValueError("repository must be an owner/name key")
    name = value.split("/")[1]
    if name in {".", ".."} or name.lower().endswith(".git"):
        raise ValueError("repository must be an unambiguous owner/name key without .git")
    return value.lower()


def _revision(value: str) -> str:
    """Require a full Git object ID, excluding branch deletion sentinels."""
    if not re.fullmatch(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})", value) or set(value) == {"0"}:
        raise ValueError("revision must be a full nonzero 40- or 64-character Git object ID")
    return value.lower()


def _branch(value: str) -> str:
    """Accept an exact short branch name, never a ref expression or wildcard."""
    _plain_text(value)
    if (
        value.startswith(("-", "refs/"))
        or value == "@"
        or ".." in value
        or "@{" in value
        or any(char in value for char in " ~^:?*[\\")
        or any(
            not part or part.startswith(".") or part.endswith((".", ".lock"))
            for part in value.split("/")
        )
    ):
        raise ValueError("branch must be an exact short branch name")
    return value


def _dns_label(value: str) -> str:
    """Keep centrally selected targets, namespaces, releases, and profiles portable."""
    if not _DNS_LABEL.fullmatch(value):
        raise ValueError("must be a lowercase DNS label of at most 63 characters")
    return value


def _relative_path(value: str) -> str:
    """Keep source paths relative to a pinned POSIX checkout on every host platform."""
    _plain_text(value)
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "\\" in value or ":" in value:
        raise ValueError("path must remain relative to the repository checkout")
    return str(path)


class PipelineServiceContract(_PipelineModel):
    """Source-owned build/runtime information with no deployment or credential authority."""

    api_version: Literal["goblin-king/pipeline-service/v1alpha1"] = Field(
        default="goblin-king/pipeline-service/v1alpha1", alias="apiVersion"
    )
    kind: Literal["PipelineService"] = "PipelineService"
    context: str = "."
    dockerfile: str = "Dockerfile"
    test_target: str = Field(default="test", pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    runtime_target: str | None = Field(default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    test_command: tuple[str, ...] = ("sh", "ci/test.sh")
    background_command: tuple[str, ...] | None = None
    scheduled_command: tuple[str, ...] | None = None
    port: int = Field(gt=0, le=65535, strict=True)
    health_path: str = Field(default="/health", min_length=1, max_length=2048)

    _paths = field_validator("context", "dockerfile")(_relative_path)

    @field_validator("background_command", "scheduled_command")
    @classmethod
    def validate_optional_command(cls, value):
        return cls.validate_test_command(value) if value is not None else None

    @field_validator("test_command")
    @classmethod
    def validate_test_command(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Represent source tests as argv; execution and identity isolation are later stages."""
        if not value or len(value) > 128:
            raise ValueError("test_command must contain between 1 and 128 arguments")
        for argument in value:
            _plain_text(argument)
            if len(argument) > 4096:
                raise ValueError("test command argument exceeds 4096 characters")
        return value

    @field_validator("health_path")
    @classmethod
    def validate_health_path(cls, value: str) -> str:
        """Accept a local HTTP path, excluding an alternate host or URL scheme."""
        _plain_text(value)
        if not value.startswith("/") or value.startswith("//") or any(c in value for c in "?#\\ "):
            raise ValueError("health_path must be a local absolute path without query or fragment")
        return value


@dataclass(frozen=True)
class ServiceCheckoutPaths:
    """Resolved build inputs, checked for containment but not yet executed."""

    context: Path
    dockerfile: Path


def validate_service_checkout(
    contract: PipelineServiceContract, checkout: str | Path
) -> ServiceCheckoutPaths:
    """Check existing build inputs and resolved symlinks within an already pinned checkout.

    Both configured paths are repository-relative. This does not fetch source,
    execute tests, audit Dockerfile contents, or eliminate later filesystem races.
    The eventual builder must own an isolated immutable checkout during execution.
    """
    root = Path(checkout).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("checkout must be an existing directory")
    context = (root / contract.context).resolve()
    dockerfile = (root / contract.dockerfile).resolve()
    if not context.is_relative_to(root) or not dockerfile.is_relative_to(root):
        raise ValueError("build input resolves outside the repository checkout")
    if not context.is_dir():
        raise ValueError("build context must be an existing directory")
    if not dockerfile.is_file():
        raise ValueError("Dockerfile must be an existing file")
    return ServiceCheckoutPaths(context=context, dockerfile=dockerfile)


class PipelineEnvironment(_PipelineModel):
    """Operator-owned route to an approved deployment profile and image destination."""

    name: EnvironmentName
    branch: str = Field(min_length=1, max_length=255)
    target: str
    namespace: str
    release: str = Field(max_length=53)
    deployment_profile: str
    image_repository: str = Field(min_length=1, max_length=255)
    active: bool = Field(default=True, strict=True)

    _branch_name = field_validator("branch")(_branch)
    _target_names = field_validator("target", "namespace", "release", "deployment_profile")(
        _dns_label
    )

    @field_validator("image_repository")
    @classmethod
    def validate_image_repository(cls, value: str) -> str:
        """Require an explicit registry and repository path without a tag or digest."""
        host, separator, path = value.partition("/")
        hostname, colon, port = host.partition(":")
        if (
            not separator
            or not re.fullmatch(r"[a-z0-9]+(?:[.-][a-z0-9]+)*", hostname)
            or not ("." in hostname or hostname == "localhost" or colon)
            or (
                colon
                and (not port.isascii() or not port.isdecimal() or not 1 <= int(port) <= 65535)
            )
            or not all(_IMAGE_PATH.fullmatch(part) for part in path.split("/"))
        ):
            raise ValueError("image_repository must be a qualified untagged registry path")
        return value

    @model_validator(mode="after")
    def validate_activation(self) -> PipelineEnvironment:
        """Keep the initial activation switch scoped to test releases."""
        if self.name == "prod" and not self.active:
            raise ValueError("production cannot use the test deactivation switch")
        return self


class SourceChange(_PipelineModel):
    """Normalized source event; the caller must authenticate and authorize its provenance."""

    provider: Literal["github"] = "github"
    delivery_id: str = Field(min_length=1, max_length=200)
    repository: str
    branch: str = Field(min_length=1, max_length=255)
    commit: str
    actor: str = Field(min_length=1, max_length=200)

    _repository_key = field_validator("repository")(_repository)
    _commit_id = field_validator("commit")(_revision)
    _branch_name = field_validator("branch")(_branch)
    _identifiers = field_validator("delivery_id", "actor")(_plain_text)


class PipelineRegistration(_PipelineModel):
    """Immutable operator registration; source repositories cannot create this authorization."""

    project_id: str = Field(min_length=1, max_length=200)
    service_id: str
    provider: Literal["github"] = "github"
    source_repository: str
    pipeline_revision: str
    catalog_revision: str
    environments: tuple[PipelineEnvironment, ...] = Field(min_length=1, max_length=2)

    _project_key = field_validator("project_id")(_plain_text)
    _service_key = field_validator("service_id")(_dns_label)
    _repository_key = field_validator("source_repository")(_repository)
    _revisions = field_validator("pipeline_revision", "catalog_revision")(_revision)

    @model_validator(mode="after")
    def validate_environment_routes(self) -> PipelineRegistration:
        """Reject ambiguous branches, shared release identities, and image destinations."""
        for field in ("name", "branch", "image_repository"):
            values = [getattr(environment, field) for environment in self.environments]
            if len(values) != len(set(values)):
                raise ValueError(f"environments must have distinct {field} values")
        releases = {(env.target, env.namespace, env.release) for env in self.environments}
        if len(releases) != len(self.environments):
            raise ValueError("environments must have distinct release targets")
        return self

    def environment_for(self, change: SourceChange) -> PipelineEnvironment:
        """Route an authenticated source change using the exact approved repository and branch."""
        if self.provider != change.provider or self.source_repository != change.repository:
            raise ValueError("source repository is not registered")
        for environment in self.environments:
            if environment.branch == change.branch:
                return environment
        raise ValueError("source branch is not registered")


class PipelineCatalog(_PipelineModel):
    """One operator catalog with exclusive source, release, and publication routes."""

    api_version: Literal["goblin-king/pipeline-catalog/v1alpha1"] = Field(
        default="goblin-king/pipeline-catalog/v1alpha1", alias="apiVersion"
    )
    kind: Literal["PipelineCatalog"] = "PipelineCatalog"
    registrations: tuple[PipelineRegistration, ...] = ()

    @model_validator(mode="after")
    def validate_exclusive_routes(self) -> PipelineCatalog:
        """Catch conflicts across registrations before any controller accepts the catalog."""
        identities, repositories, releases, images = set(), set(), set(), set()
        for registration in self.registrations:
            identity = (registration.project_id, registration.service_id)
            repository = (registration.provider, registration.source_repository)
            if identity in identities or repository in repositories:
                raise ValueError("catalog contains duplicate service or repository registrations")
            identities.add(identity)
            repositories.add(repository)
            for environment in registration.environments:
                release = (environment.target, environment.namespace, environment.release)
                if release in releases or environment.image_repository in images:
                    raise ValueError("catalog contains conflicting release or image destinations")
                releases.add(release)
                images.add(environment.image_repository)
        return self


class PipelineBuildRequest(_PipelineModel):
    """Durable admitted intent, not proof of worker execution or image publication."""

    sequence: int = Field(gt=0)
    build_id: str
    created_at: datetime
    registration: PipelineRegistration
    source: SourceChange
    environment_name: EnvironmentName

    @field_validator("build_id")
    @classmethod
    def validate_build_id(cls, value: str) -> str:
        """Use a canonical UUID as the future immutable image tag."""
        parsed = UUID(value)
        if parsed.version != 4 or str(parsed) != value:
            raise ValueError("build_id must be a canonical UUIDv4")
        return value

    @field_validator("created_at")
    @classmethod
    def validate_timestamp(cls, value: datetime) -> datetime:
        """Reject timezone-ambiguous durable admission timestamps."""
        if value.utcoffset() is None:
            raise ValueError("created_at must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_route(self) -> PipelineBuildRequest:
        """Ensure persisted routing agrees with the immutable registration snapshot."""
        if self.registration.environment_for(self.source).name != self.environment_name:
            raise ValueError("request environment does not match the registered source branch")
        return self

    @property
    def environment(self) -> PipelineEnvironment:
        """Return the admission snapshot; deployment must later re-read current desired state."""
        return self.registration.environment_for(self.source)

    @property
    def image_tag(self) -> str:
        """Return the reserved tag without implying that an image exists yet."""
        return self.build_id
