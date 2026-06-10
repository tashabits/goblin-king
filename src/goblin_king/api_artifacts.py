"""Artifact path, status, and cleanup helpers for the API control plane."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from goblin_king.api_models import ArtifactCleanupRequest
from goblin_king.contracts import ArtifactRecord
from goblin_king.store import SQLiteStore


def artifact_file_path(root: Path, artifact: ArtifactRecord) -> Path | None:
    """Resolve a file artifact only when it stays inside the configured artifact root."""
    if artifact.uri.startswith("file://"):
        candidate = Path(artifact.uri.removeprefix("file://"))
    elif "://" in artifact.uri:
        return None
    else:
        candidate = Path(artifact.uri)
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        resolved = candidate.resolve()
        resolved.relative_to(root)
    except ValueError:
        return None
    return resolved


def artifact_storage_status(
    store: SQLiteStore,
    root: Path,
    *,
    project_id: str | None,
) -> dict[str, Any]:
    """Return volume/PVC artifact root status and scoped metadata counts."""
    files = artifact_files_under(root)
    return {
        "root": str(root),
        "exists": root.exists(),
        "writable": path_is_writable(root),
        "file_count": len(files),
        "total_bytes": sum(path.stat().st_size for path in files),
        "metadata_count": len(store.list_artifacts_for_project(project_id)),
    }


def cleanup_artifact_files(
    store: SQLiteStore,
    root: Path,
    request: ArtifactCleanupRequest,
    *,
    project_id: str | None,
) -> dict[str, Any]:
    """Select and optionally delete artifact files from the configured volume/PVC."""
    candidates = project_artifact_paths(store, root, project_id=project_id)
    selected = select_artifact_cleanup_candidates(
        candidates,
        max_age_seconds=request.max_age_seconds,
        max_total_bytes=request.max_total_bytes,
    )
    bytes_selected = sum(path.stat().st_size for path in selected if path.exists())
    if not request.dry_run:
        for path in selected:
            try:
                path.unlink()
            except FileNotFoundError:
                continue
    return {
        "dry_run": request.dry_run,
        "deleted": not request.dry_run,
        "root": str(root),
        "files_selected": len(selected),
        "bytes_selected": bytes_selected,
        "files": [str(path.relative_to(root)) for path in selected],
    }


def project_artifact_paths(
    store: SQLiteStore,
    root: Path,
    *,
    project_id: str | None,
) -> list[Path]:
    """Resolve scoped artifact metadata into safe local files."""
    paths: list[Path] = []
    for artifact in store.list_artifacts_for_project(project_id):
        path = artifact_file_path(root, artifact)
        if path is not None and path.exists() and path.is_file():
            paths.append(path)
    return sorted(set(paths), key=lambda item: item.stat().st_mtime)


def select_artifact_cleanup_candidates(
    paths: list[Path],
    *,
    max_age_seconds: int | None,
    max_total_bytes: int | None,
) -> list[Path]:
    """Apply age and volume-size cleanup policies to artifact files."""
    selected: set[Path] = set()
    now = datetime.now().timestamp()
    if max_age_seconds is not None:
        selected.update(
            path for path in paths if now - path.stat().st_mtime >= max_age_seconds
        )
    if max_total_bytes is not None:
        total = sum(path.stat().st_size for path in paths)
        for path in paths:
            if total <= max_total_bytes:
                break
            selected.add(path)
            total -= path.stat().st_size
    return sorted(selected, key=lambda item: item.stat().st_mtime)


def artifact_files_under(root: Path) -> list[Path]:
    """Return files below the artifact root without following external references."""
    if not root.exists():
        return []
    return [path for path in root.rglob("*") if path.is_file()]


def path_is_writable(root: Path) -> bool:
    """Return whether the artifact root can accept files."""
    try:
        root.mkdir(parents=True, exist_ok=True)
        probe = root / ".goblin-king-write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False
