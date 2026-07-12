"""Resolve writable Docker worker paths independently of the process working directory."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

DEFAULT_DOCKER_RUN_ROOT = Path(".goblin-king") / "runs"
DOCKER_DATA_VOLUME_ENV = "GOBLIN_KING_DOCKER_DATA_VOLUME"
DOCKER_RUN_ROOT_ENV = "GOBLIN_KING_RUN_ROOT"


class DockerRuntimePathError(ValueError):
    """Report an unsafe or incomplete Docker worker path configuration."""


def configured_docker_run_root(
    run_root: str | Path | None = None,
    *,
    environment: Mapping[str, str] | None = None,
) -> Path | None:
    """Return the explicit argument or environment value without applying a default."""
    if run_root is not None:
        return Path(run_root)
    environ = os.environ if environment is None else environment
    value = environ.get(DOCKER_RUN_ROOT_ENV)
    return Path(value) if value else None


def resolve_docker_run_root(
    run_root: str | Path | None = None,
    *,
    environment: Mapping[str, str] | None = None,
) -> Path:
    """Resolve the host-visible worker run root and validate named-volume usage."""
    environ = os.environ if environment is None else environment
    resolved = configured_docker_run_root(run_root, environment=environ)
    data_volume = environ.get(DOCKER_DATA_VOLUME_ENV)
    if resolved is None:
        if data_volume:
            raise DockerRuntimePathError(
                f"{DOCKER_RUN_ROOT_ENV} is required when {DOCKER_DATA_VOLUME_ENV} is set; "
                "configure an absolute writable path inside the shared data-volume mount"
            )
        resolved = DEFAULT_DOCKER_RUN_ROOT
    if data_volume and not resolved.is_absolute():
        raise DockerRuntimePathError(
            f"{DOCKER_RUN_ROOT_ENV} must be absolute when {DOCKER_DATA_VOLUME_ENV} is set: "
            f"{resolved}"
        )
    return resolved


def resolve_docker_artifact_root(run_root: Path, artifact_root: str | Path) -> Path:
    """Resolve relative artifact paths beneath the configured Docker data root."""
    configured = Path(artifact_root)
    if configured.is_absolute():
        return configured.resolve()
    try:
        relative = configured.relative_to(".goblin-king")
    except ValueError:
        relative = configured
    return (run_root.resolve().parent / relative).resolve()


def relative_to_docker_data_root(path: Path, run_root: Path, *, label: str) -> str:
    """Return a container-mount-relative path or raise an actionable configuration error."""
    data_root = run_root.resolve().parent
    try:
        return path.resolve().relative_to(data_root).as_posix()
    except ValueError as error:
        raise DockerRuntimePathError(
            f"{label} must be inside Docker data root {data_root}: {path.resolve()}"
        ) from error
