"""Configuration contracts for durable Kubernetes artifact retention."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

DEFAULT_ARTIFACT_MAX_BYTES = 100 * 1024 * 1024
DEFAULT_ARTIFACT_MAX_FILES = 100
ARTIFACT_SOURCE_ROOT = "/artifacts"
ARTIFACT_VOLUME_MOUNT_PATH = "/goblin-artifact-volume"
ARTIFACT_PVC_CLAIM_ENV = "GOBLIN_KING_K8S_ARTIFACT_PVC_CLAIM"
ARTIFACT_VOLUME_SUBDIRECTORY_ENV = "GOBLIN_KING_K8S_ARTIFACT_VOLUME_SUBDIRECTORY"
ARTIFACT_URI_ROOT_ENV = "GOBLIN_KING_K8S_ARTIFACT_URI_ROOT"
ARTIFACT_DESTINATION_ROOT_ENV = "GOBLIN_ARTIFACT_DESTINATION_ROOT"
ARTIFACT_SOURCE_ROOT_ENV = "GOBLIN_ARTIFACT_SOURCE_ROOT"
ARTIFACT_PROJECT_ID_ENV = "GOBLIN_ARTIFACT_PROJECT_ID"
ARTIFACT_MAX_BYTES_ENV = "GOBLIN_ARTIFACT_MAX_BYTES"
ARTIFACT_MAX_FILES_ENV = "GOBLIN_ARTIFACT_MAX_FILES"

_CLAIM_NAME = re.compile(r"^[a-z0-9](?:[-a-z0-9.]*[a-z0-9])?$")


class ArtifactRetentionError(ValueError):
    """Report a safe, user-visible artifact retention failure."""


@dataclass(frozen=True)
class KubernetesArtifactRetention:
    """Describe the operator-owned PVC projection used by result forwarders."""

    claim_name: str
    volume_subdirectory: str = "artifacts"
    uri_root: str = "/data/artifacts"
    volume_mount_path: str = ARTIFACT_VOLUME_MOUNT_PATH

    def __post_init__(self) -> None:
        if len(self.claim_name) > 253 or not _CLAIM_NAME.fullmatch(self.claim_name):
            raise ValueError("artifact PVC claim must be a valid Kubernetes resource name")
        _validate_absolute_posix(self.volume_mount_path, "artifact volume mount path")
        _validate_absolute_posix(self.uri_root, "artifact URI root")
        subdirectory = PurePosixPath(self.volume_subdirectory)
        if (
            subdirectory.is_absolute()
            or subdirectory == PurePosixPath(".")
            or ".." in subdirectory.parts
        ):
            raise ValueError("artifact volume subdirectory must be a non-empty relative path")

    @property
    def destination_root(self) -> str:
        """Return the sidecar-visible directory receiving retained bytes."""
        return str(PurePosixPath(self.volume_mount_path) / self.volume_subdirectory)

    @classmethod
    def from_environment(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> KubernetesArtifactRetention | None:
        """Load optional Kubernetes artifact retention from scheduler environment."""
        values = os.environ if environ is None else environ
        claim_name = values.get(ARTIFACT_PVC_CLAIM_ENV, "").strip()
        if not claim_name:
            return None
        return cls(
            claim_name=claim_name,
            volume_subdirectory=values.get(
                ARTIFACT_VOLUME_SUBDIRECTORY_ENV,
                "artifacts",
            ),
            uri_root=values.get(ARTIFACT_URI_ROOT_ENV, "/data/artifacts"),
        )


@dataclass(frozen=True)
class ArtifactRetentionRequest:
    """Provide one forwarder's validated source, destination, scope, and limits."""

    source_root: Path
    destination_root: Path | None
    uri_root: str | None
    run_id: str
    project_id: str | None = None
    max_files: int = DEFAULT_ARTIFACT_MAX_FILES
    max_bytes: int = DEFAULT_ARTIFACT_MAX_BYTES

    @classmethod
    def from_environment(
        cls,
        run_id: str,
        environ: Mapping[str, str] | None = None,
    ) -> ArtifactRetentionRequest:
        """Build a request from the narrow environment passed to the sidecar."""
        values = os.environ if environ is None else environ
        destination = values.get(ARTIFACT_DESTINATION_ROOT_ENV, "").strip()
        uri_root = values.get(ARTIFACT_URI_ROOT_ENV, "").strip()
        return cls(
            source_root=Path(values.get(ARTIFACT_SOURCE_ROOT_ENV, ARTIFACT_SOURCE_ROOT)),
            destination_root=Path(destination) if destination else None,
            uri_root=uri_root or None,
            run_id=run_id,
            project_id=values.get(ARTIFACT_PROJECT_ID_ENV) or None,
            max_files=_environment_limit(
                values,
                ARTIFACT_MAX_FILES_ENV,
                DEFAULT_ARTIFACT_MAX_FILES,
            ),
            max_bytes=_environment_limit(
                values,
                ARTIFACT_MAX_BYTES_ENV,
                DEFAULT_ARTIFACT_MAX_BYTES,
            ),
        )


def _validate_absolute_posix(value: str, label: str) -> None:
    path = PurePosixPath(value)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError(f"{label} must be an absolute normalized path")


def _environment_limit(values: Mapping[str, str], name: str, default: int) -> int:
    try:
        value = int(values.get(name, str(default)))
    except ValueError as error:
        raise ValueError(f"{name} must be an integer") from error
    if value < 0:
        raise ValueError(f"{name} must be non-negative")
    return value
