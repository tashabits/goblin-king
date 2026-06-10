"""Load, validate, and apply per-goblin runtime resource policies."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class ResourcePolicyError(ValueError):
    """Raised when a resource policy file or effective policy is invalid."""


class CpuPolicy(BaseModel):
    """CPU request/limit fields shared by Docker and Kubernetes mappings."""

    request: str | None = None
    limit: str | None = None


class MemoryPolicy(BaseModel):
    """Memory request/limit fields shared by Docker and Kubernetes mappings."""

    request: str | None = None
    limit: str | None = None


class ProcessPolicy(BaseModel):
    """Process limits supported by Docker where available."""

    pids_limit: int | None = Field(default=None, gt=0)


class NetworkPolicy(BaseModel):
    """Network mode for a worker container."""

    mode: str | None = None


class FilesystemPolicy(BaseModel):
    """Filesystem and artifact limits for worker execution."""

    read_only_root: bool | None = None
    tmpfs: list[str] = Field(default_factory=list)
    artifact_max_bytes: int | None = Field(default=None, ge=0)
    artifact_max_files: int | None = Field(default=None, ge=0)


class LogsPolicy(BaseModel):
    """Log byte preservation ceiling for captured runtime output."""

    max_bytes: int | None = Field(default=None, ge=0)


class ConcurrencyPolicy(BaseModel):
    """Per-kind concurrency limit metadata."""

    max_running: int | None = Field(default=None, gt=0)


class ResourcePolicy(BaseModel):
    """Effective policy values attached to jobs and runs."""

    timeout_seconds: int | None = Field(default=None, gt=0)
    max_retries: int | None = Field(default=None, ge=0)
    cpu: CpuPolicy = Field(default_factory=CpuPolicy)
    memory: MemoryPolicy = Field(default_factory=MemoryPolicy)
    process: ProcessPolicy = Field(default_factory=ProcessPolicy)
    network: NetworkPolicy = Field(default_factory=NetworkPolicy)
    filesystem: FilesystemPolicy = Field(default_factory=FilesystemPolicy)
    logs: LogsPolicy = Field(default_factory=LogsPolicy)
    concurrency: ConcurrencyPolicy = Field(default_factory=ConcurrencyPolicy)

    def compact(self) -> dict[str, Any]:
        """Return a JSON-ready policy without unset or empty sections."""
        return self.model_dump(mode="json", exclude_none=True, exclude_defaults=True)


class ResourcePolicySet(BaseModel):
    """A complete resource policy file with defaults, goblin overrides, and ceilings."""

    version: int = 1
    defaults: ResourcePolicy = Field(default_factory=ResourcePolicy)
    goblins: dict[str, ResourcePolicy] = Field(default_factory=dict)
    ceilings: ResourcePolicy = Field(default_factory=ResourcePolicy)

    @classmethod
    def from_path(cls, path: str | Path) -> ResourcePolicySet:
        """Load one JSON resource policy file."""
        policy_path = Path(path)
        try:
            payload = json.loads(policy_path.read_text(encoding="utf-8"))
        except FileNotFoundError as error:
            raise ResourcePolicyError(f"resource policy file not found: {policy_path}") from error
        except json.JSONDecodeError as error:
            raise ResourcePolicyError(
                f"resource policy file is not valid JSON: {policy_path}"
            ) from error
        try:
            policies = cls.model_validate(payload)
        except ValueError as error:
            raise ResourcePolicyError(str(error)) from error
        if policies.version != 1:
            raise ResourcePolicyError(f"unsupported resource policy version: {policies.version}")
        return policies

    def effective_for(
        self,
        kind: str,
        *,
        timeout_seconds: int | None = None,
        max_retries: int | None = None,
    ) -> ResourcePolicy:
        """Resolve defaults plus a per-kind override and validate against ceilings."""
        merged = _deep_merge(
            self.defaults.model_dump(mode="json", exclude_none=True),
            self.goblins.get(kind, ResourcePolicy()).model_dump(mode="json", exclude_none=True),
        )
        if timeout_seconds is not None:
            merged["timeout_seconds"] = timeout_seconds
        if max_retries is not None:
            merged["max_retries"] = max_retries
        policy = ResourcePolicy.model_validate(merged)
        self.validate_within_ceilings(kind, policy)
        return policy

    def validate_within_ceilings(self, kind: str, policy: ResourcePolicy) -> None:
        """Reject one effective policy when it exceeds configured ceilings."""
        ceiling = self.ceilings
        checks = [
            ("timeout_seconds", policy.timeout_seconds, ceiling.timeout_seconds),
            ("max_retries", policy.max_retries, ceiling.max_retries),
            ("cpu.limit", _parse_cpu(policy.cpu.limit), _parse_cpu(ceiling.cpu.limit)),
            (
                "memory.limit",
                _parse_bytes(policy.memory.limit),
                _parse_bytes(ceiling.memory.limit),
            ),
            (
                "process.pids_limit",
                policy.process.pids_limit,
                ceiling.process.pids_limit,
            ),
            (
                "filesystem.artifact_max_bytes",
                policy.filesystem.artifact_max_bytes,
                ceiling.filesystem.artifact_max_bytes,
            ),
            (
                "filesystem.artifact_max_files",
                policy.filesystem.artifact_max_files,
                ceiling.filesystem.artifact_max_files,
            ),
            ("logs.max_bytes", policy.logs.max_bytes, ceiling.logs.max_bytes),
            (
                "concurrency.max_running",
                policy.concurrency.max_running,
                ceiling.concurrency.max_running,
            ),
        ]
        for field, value, max_value in checks:
            if value is not None and max_value is not None and value > max_value:
                raise ResourcePolicyError(
                    f"{kind} resource policy exceeds ceiling for {field}: {value} > {max_value}"
                )


def policy_from_job_metadata(metadata: dict[str, Any]) -> ResourcePolicy | None:
    """Return a ResourcePolicy from persisted job metadata when present."""
    payload = metadata.get("resource_policy")
    if payload is None:
        return None
    try:
        return ResourcePolicy.model_validate(payload)
    except ValueError as error:
        raise ResourcePolicyError(f"invalid persisted resource policy: {error}") from error


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Merge nested policy dictionaries without mutating either input."""
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _parse_cpu(value: str | None) -> float | None:
    """Parse Kubernetes-style CPU strings into core units."""
    if value is None:
        return None
    if value.endswith("m"):
        return float(value[:-1]) / 1000
    return float(value)


_BYTES_RE = re.compile(r"^(?P<number>[0-9]+)(?P<unit>Ki|Mi|Gi|Ti|K|M|G|T)?$")
_BYTE_MULTIPLIERS = {
    None: 1,
    "K": 1000,
    "M": 1000**2,
    "G": 1000**3,
    "T": 1000**4,
    "Ki": 1024,
    "Mi": 1024**2,
    "Gi": 1024**3,
    "Ti": 1024**4,
}


def _parse_bytes(value: str | None) -> int | None:
    """Parse Kubernetes/Docker memory strings into bytes for ceiling comparisons."""
    if value is None:
        return None
    match = _BYTES_RE.match(value)
    if not match:
        raise ResourcePolicyError(f"invalid byte quantity: {value}")
    return int(match.group("number")) * _BYTE_MULTIPLIERS[match.group("unit")]
