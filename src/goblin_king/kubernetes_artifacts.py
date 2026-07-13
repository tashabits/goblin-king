"""Validate and retain Kubernetes worker artifacts before transient Pod cleanup."""

from __future__ import annotations

import hashlib
import mimetypes
import os
import re
import shutil
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import quote, unquote, urlsplit

from goblin_king.artifact_storage import (
    SHARED_ARTIFACT_DIRECTORY_MODE,
    SHARED_ARTIFACT_FILE_MODE,
)
from goblin_king.contracts import ArtifactRecord, GoblinResult
from goblin_king.kubernetes_artifact_config import (
    ArtifactRetentionError,
    ArtifactRetentionRequest,
)

_MEDIA_TYPE = re.compile(
    r"^[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+"
    r"(?:\s*;\s*[A-Za-z0-9!#$&^_.+-]+=[A-Za-z0-9!#$&^_.+\-\" ]+)*$"
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_SUFFIX = re.compile(r"^\.[A-Za-z0-9]{1,10}$")


@dataclass(frozen=True)
class _ArtifactSource:
    root: Path
    relative: Path
    resolved: Path
    validated_identity: tuple[int, int, int, int]


def retain_result_artifacts(
    result: GoblinResult,
    request: ArtifactRetentionRequest,
) -> GoblinResult:
    """Atomically retain every declared artifact or return a metadata-free failure."""
    if not result.artifacts:
        return result
    try:
        artifacts, metrics = _retain_all(result, request)
    except ArtifactRetentionError as error:
        return artifact_retention_failure(result, str(error))
    except OSError:
        return artifact_retention_failure(result, "artifact storage I/O did not complete")
    return result.model_copy(update={"artifacts": artifacts, "metrics": metrics})


def artifact_retention_failure(result: GoblinResult, reason: str) -> GoblinResult:
    """Return an explicit failed envelope that cannot persist unretained metadata."""
    message = f"artifact retention failed: {reason}"
    if result.error:
        message = f"{result.error}; {message}"
    return GoblinResult.failed(
        error=message,
        data=result.data,
        metrics={
            key: value for key, value in result.metrics.items() if not key.startswith("artifact.")
        },
        handoff=result.handoff,
    )


def _retain_all(
    result: GoblinResult,
    request: ArtifactRetentionRequest,
) -> tuple[list[ArtifactRecord], dict[str, int | float | str | bool | None]]:
    if request.destination_root is None or request.uri_root is None:
        raise ArtifactRetentionError("durable Kubernetes artifact storage is not configured")
    if request.max_files < 0 or request.max_bytes < 0:
        raise ArtifactRetentionError("artifact limits must be non-negative")
    if len(result.artifacts) > request.max_files:
        raise ArtifactRetentionError(
            f"artifact file count exceeds policy: {len(result.artifacts)} > {request.max_files}"
        )
    names = [artifact.name for artifact in result.artifacts]
    if len(names) != len(set(names)):
        raise ArtifactRetentionError("artifact names must be unique within a result")

    source_root = request.source_root.resolve(strict=True)
    destination_root = request.destination_root
    destination_root.mkdir(parents=True, exist_ok=True)
    destination_root = destination_root.resolve(strict=True)
    relative_directory = _retained_directory(request.project_id, request.run_id)
    final_directory = destination_root / relative_directory
    _ensure_shared_directory_tree(destination_root, final_directory.parent)
    staging = Path(
        tempfile.mkdtemp(
            prefix=f".{relative_directory.name}.staging-",
            dir=final_directory.parent,
        )
    )
    staging.chmod(SHARED_ARTIFACT_DIRECTORY_MODE)
    retained: list[ArtifactRecord] = []
    metrics = dict(result.metrics)
    total_bytes = 0
    try:
        for index, artifact in enumerate(result.artifacts):
            _validate_artifact_name(artifact.name)
            source = _resolve_source(source_root, artifact)
            stored_name = _stored_name(index, artifact.name)
            size, digest = _copy_and_hash(
                source,
                staging / stored_name,
                request.max_bytes - total_bytes,
                artifact.name,
            )
            _verify_declared_metrics(result, artifact.name, size, digest)
            total_bytes += size
            media_type = _validated_media_type(artifact)
            retained.append(
                ArtifactRecord(
                    name=artifact.name,
                    uri=_retained_uri(
                        request.uri_root,
                        relative_directory,
                        stored_name,
                    ),
                    media_type=media_type,
                )
            )
            metrics[f"artifact.{artifact.name}.bytes"] = size
            metrics[f"artifact.{artifact.name}.sha256"] = digest
        _commit_staging(staging, final_directory)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    metrics["artifact.retained.files"] = len(retained)
    metrics["artifact.retained.bytes"] = total_bytes
    return retained, metrics


def _resolve_source(source_root: Path, artifact: ArtifactRecord) -> _ArtifactSource:
    parsed = urlsplit(artifact.uri)
    if parsed.scheme and parsed.scheme != "file":
        raise ArtifactRetentionError(f"artifact {artifact.name!r} must use a local file URI")
    if parsed.scheme == "file" and parsed.netloc not in {"", "localhost"}:
        raise ArtifactRetentionError(f"artifact {artifact.name!r} has a remote file authority")
    raw_path = unquote(parsed.path) if parsed.scheme == "file" else artifact.uri
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = source_root / candidate
    try:
        unresolved_relative = candidate.relative_to(source_root)
    except ValueError as error:
        raise ArtifactRetentionError(
            f"artifact {artifact.name!r} is missing or outside the worker artifact root"
        ) from error
    if ".." in unresolved_relative.parts:
        raise ArtifactRetentionError(
            f"artifact {artifact.name!r} is missing or outside the worker artifact root"
        )
    _reject_symbolic_link_components(source_root, unresolved_relative, artifact.name)
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(source_root)
    except (FileNotFoundError, ValueError) as error:
        raise ArtifactRetentionError(
            f"artifact {artifact.name!r} is missing or outside the worker artifact root"
        ) from error
    source_stat = resolved.stat()
    if not stat.S_ISREG(source_stat.st_mode):
        raise ArtifactRetentionError(f"artifact {artifact.name!r} is not a regular file")
    return _ArtifactSource(
        root=source_root,
        relative=unresolved_relative,
        resolved=resolved,
        validated_identity=_opened_file_identity(source_stat),
    )


def _reject_symbolic_link_components(root: Path, relative: Path, name: str) -> None:
    current = root
    for part in relative.parts:
        current /= part
        try:
            mode = current.lstat().st_mode
        except OSError as error:
            raise ArtifactRetentionError(
                f"artifact {name!r} is missing or outside the worker artifact root"
            ) from error
        if stat.S_ISLNK(mode):
            raise ArtifactRetentionError(f"artifact {name!r} may not use symbolic links")


def _copy_and_hash(
    source: _ArtifactSource,
    destination: Path,
    remaining: int,
    name: str,
) -> tuple[int, str]:
    if os.name == "posix" and hasattr(os, "O_NOFOLLOW"):
        return _copy_and_hash_posix(source, destination, remaining, name)
    return _copy_and_hash_portable(source, destination, remaining, name)


def _copy_and_hash_posix(
    source: _ArtifactSource,
    destination: Path,
    remaining: int,
    name: str,
) -> tuple[int, str]:
    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    file_flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NONBLOCK", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptors: list[int] = []
    try:
        parent_fd = os.open(source.root, directory_flags)
        descriptors.append(parent_fd)
        for component in source.relative.parts[:-1]:
            parent_fd = os.open(component, directory_flags, dir_fd=parent_fd)
            descriptors.append(parent_fd)
        source_fd = os.open(source.relative.parts[-1], file_flags, dir_fd=parent_fd)
        descriptors.append(source_fd)
    except OSError as error:
        _close_descriptors(descriptors)
        raise ArtifactRetentionError(
            f"artifact {name!r} changed or became unsafe before retention"
        ) from error
    try:
        return _copy_fd_and_hash(
            source_fd,
            destination,
            remaining,
            name,
            source.validated_identity,
        )
    finally:
        _close_descriptors(descriptors)


def _close_descriptors(descriptors: list[int]) -> None:
    for descriptor in reversed(descriptors):
        os.close(descriptor)


def _copy_and_hash_portable(
    source: _ArtifactSource,
    destination: Path,
    remaining: int,
    name: str,
) -> tuple[int, str]:
    _reject_symbolic_link_components(source.root, source.relative, name)
    with source.resolved.open("rb") as source_file:
        return _copy_fd_and_hash(
            source_file.fileno(),
            destination,
            remaining,
            name,
            source.validated_identity,
        )


def _copy_fd_and_hash(
    source_fd: int,
    destination: Path,
    remaining: int,
    name: str,
    validated_identity: tuple[int, int, int, int],
) -> tuple[int, str]:
    before = os.fstat(source_fd)
    if not stat.S_ISREG(before.st_mode):
        raise ArtifactRetentionError(f"artifact {name!r} is not a regular file")
    if _opened_file_identity(before) != validated_identity:
        raise ArtifactRetentionError(
            f"artifact {name!r} changed or became unsafe before retention"
        )
    digest = hashlib.sha256()
    size = 0
    with destination.open("xb") as destination_file:
        if hasattr(os, "fchmod"):
            os.fchmod(destination_file.fileno(), SHARED_ARTIFACT_FILE_MODE)
        else:  # pragma: no cover - Windows mode support is intentionally conservative
            destination.chmod(SHARED_ARTIFACT_FILE_MODE)
        while chunk := os.read(source_fd, 1024 * 1024):
            size += len(chunk)
            if size > remaining:
                raise ArtifactRetentionError("artifact bytes exceed policy")
            digest.update(chunk)
            destination_file.write(chunk)
    after = os.fstat(source_fd)
    identity_before = _file_identity(before)
    identity_after = _file_identity(after)
    if identity_before != identity_after or size != after.st_size:
        raise ArtifactRetentionError(f"artifact {name!r} changed while it was retained")
    return size, digest.hexdigest()


def _file_identity(details: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        details.st_dev,
        details.st_ino,
        details.st_size,
        details.st_mtime_ns,
        details.st_ctime_ns,
    )


def _opened_file_identity(details: os.stat_result) -> tuple[int, int, int, int]:
    return (
        details.st_dev,
        details.st_ino,
        details.st_size,
        details.st_mtime_ns,
    )


def _verify_declared_metrics(
    result: GoblinResult,
    name: str,
    actual_size: int,
    actual_digest: str,
) -> None:
    declared_size = result.metrics.get(f"artifact.{name}.bytes")
    if declared_size is not None and (
        isinstance(declared_size, bool)
        or not isinstance(declared_size, int)
        or declared_size != actual_size
    ):
        raise ArtifactRetentionError(f"artifact {name!r} byte count does not match its metadata")
    declared_digest = result.metrics.get(f"artifact.{name}.sha256")
    if declared_digest is None:
        return
    if not isinstance(declared_digest, str):
        raise ArtifactRetentionError(f"artifact {name!r} SHA-256 metadata is invalid")
    normalized = declared_digest.lower().removeprefix("sha256:")
    if not _SHA256.fullmatch(normalized) or normalized != actual_digest:
        raise ArtifactRetentionError(f"artifact {name!r} SHA-256 digest does not match")


def _validated_media_type(artifact: ArtifactRecord) -> str:
    media_type = artifact.media_type or mimetypes.guess_type(artifact.name)[0]
    media_type = media_type or "application/octet-stream"
    if len(media_type) > 255 or not _MEDIA_TYPE.fullmatch(media_type):
        raise ArtifactRetentionError(f"artifact {artifact.name!r} media type is invalid")
    return media_type


def _commit_staging(staging: Path, final_directory: Path) -> None:
    if final_directory.exists():
        if not _directories_match(staging, final_directory):
            raise ArtifactRetentionError(
                "retained artifact destination already contains other bytes"
            )
        shutil.rmtree(staging)
        _apply_retained_modes(final_directory)
        return
    try:
        staging.rename(final_directory)
    except FileExistsError as error:
        if not _directories_match(staging, final_directory):
            raise ArtifactRetentionError(
                "retained artifact destination changed concurrently"
            ) from error
        shutil.rmtree(staging)
    _apply_retained_modes(final_directory)


def _ensure_shared_directory_tree(root: Path, target: Path) -> None:
    current = root
    for component in target.relative_to(root).parts:
        current /= component
        current.mkdir(mode=SHARED_ARTIFACT_DIRECTORY_MODE, exist_ok=True)
        if current.is_symlink() or not current.is_dir():
            raise ArtifactRetentionError("retained artifact path must contain only directories")
        current.chmod(SHARED_ARTIFACT_DIRECTORY_MODE)


def _apply_retained_modes(directory: Path) -> None:
    directory.chmod(SHARED_ARTIFACT_DIRECTORY_MODE)
    for path in directory.iterdir():
        if path.is_symlink() or not path.is_file():
            raise ArtifactRetentionError("retained artifact directory contains an unsafe entry")
        path.chmod(SHARED_ARTIFACT_FILE_MODE)


def _directories_match(left: Path, right: Path) -> bool:
    left_entries = list(left.iterdir())
    right_entries = list(right.iterdir())
    if not all(path.is_file() for path in [*left_entries, *right_entries]):
        return False
    left_files = sorted(path.name for path in left_entries)
    right_files = sorted(path.name for path in right_entries)
    if left_files != right_files:
        return False
    return all(_file_sha256(left / name) == _file_sha256(right / name) for name in left_files)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _retained_directory(project_id: str | None, run_id: str) -> Path:
    project_scope = "unscoped" if project_id is None else hashlib.sha256(
        project_id.encode("utf-8")
    ).hexdigest()
    run_scope = hashlib.sha256(run_id.encode("utf-8")).hexdigest()
    return Path("projects") / project_scope / "runs" / run_scope


def retained_run_path(uri_root: str, project_id: str | None, run_id: str) -> Path:
    """Return the deterministic local retention directory for one Run."""
    return _local_retention_root(uri_root) / _retained_directory(project_id, run_id)


def cleanup_retained_run(uri_root: str, project_id: str | None, run_id: str) -> None:
    """Delete one non-persisted validation Run and prune only its empty hash parents."""
    root = _local_retention_root(uri_root).resolve(strict=True)
    target = root / _retained_directory(project_id, run_id)
    if not target.exists():
        return
    if target.is_symlink():
        raise ArtifactRetentionError("retained validation path may not be a symbolic link")
    try:
        resolved = target.resolve(strict=True)
        resolved.relative_to(root)
    except (FileNotFoundError, ValueError) as error:
        raise ArtifactRetentionError("retained validation path escaped its root") from error
    shutil.rmtree(resolved)
    parent = resolved.parent
    while parent != root:
        try:
            parent.rmdir()
        except OSError:
            break
        parent = parent.parent


def _local_retention_root(uri_root: str) -> Path:
    direct = Path(uri_root)
    if direct.is_absolute():
        return direct
    parsed = urlsplit(uri_root)
    if parsed.scheme not in {"", "file"} or parsed.netloc not in {"", "localhost"}:
        raise ArtifactRetentionError("artifact URI root must identify local storage")
    return Path(unquote(parsed.path) if parsed.scheme == "file" else uri_root)


def _retained_uri(uri_root: str, relative_directory: Path, stored_name: str) -> str:
    encoded_relative = quote(
        str(PurePosixPath(*relative_directory.parts) / stored_name),
        safe="/",
    )
    if uri_root.startswith("file://"):
        return f"{uri_root.rstrip('/')}/{encoded_relative}"
    root = quote(str(PurePosixPath(uri_root)), safe="/:")
    return f"file://{root.rstrip('/')}/{encoded_relative}"


def _stored_name(index: int, name: str) -> str:
    suffix = Path(name).suffix
    suffix = suffix if _SAFE_SUFFIX.fullmatch(suffix) else ""
    identity = hashlib.sha256(name.encode("utf-8")).hexdigest()[:16]
    return f"{index:04d}-{identity}{suffix.lower()}"


def _validate_artifact_name(name: str) -> None:
    if (
        len(name) > 255
        or name in {".", ".."}
        or "/" in name
        or "\\" in name
        or any(ord(character) < 32 for character in name)
    ):
        raise ArtifactRetentionError("artifact names must be safe single path segments")
