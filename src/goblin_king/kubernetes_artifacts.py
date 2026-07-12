"""Validate and retain Kubernetes worker artifacts before transient Pod cleanup."""

from __future__ import annotations

import hashlib
import mimetypes
import re
import shutil
import stat
import tempfile
from pathlib import Path, PurePosixPath
from urllib.parse import quote, unquote, urlsplit

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
    final_directory.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(
            prefix=f".{relative_directory.name}.staging-",
            dir=final_directory.parent,
        )
    )
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


def _resolve_source(source_root: Path, artifact: ArtifactRecord) -> Path:
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
    if not resolved.is_file():
        raise ArtifactRetentionError(f"artifact {artifact.name!r} is not a regular file")
    return resolved


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


def _copy_and_hash(source: Path, destination: Path, remaining: int, name: str) -> tuple[int, str]:
    before = source.stat()
    digest = hashlib.sha256()
    size = 0
    with source.open("rb") as source_file, destination.open("xb") as destination_file:
        while chunk := source_file.read(1024 * 1024):
            size += len(chunk)
            if size > remaining:
                raise ArtifactRetentionError("artifact bytes exceed policy")
            digest.update(chunk)
            destination_file.write(chunk)
    after = source.stat()
    identity_before = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    identity_after = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    if identity_before != identity_after or size != after.st_size:
        raise ArtifactRetentionError(f"artifact {name!r} changed while it was retained")
    return size, digest.hexdigest()


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
        return
    try:
        staging.rename(final_directory)
    except FileExistsError as error:
        if not _directories_match(staging, final_directory):
            raise ArtifactRetentionError(
                "retained artifact destination changed concurrently"
            ) from error
        shutil.rmtree(staging)


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
