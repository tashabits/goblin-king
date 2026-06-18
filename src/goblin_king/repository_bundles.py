"""Safe upload bundle parsing for the browser-facing repository UI."""

from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

from goblin_king.contracts import GOBLIN_KIND_PATTERN, RepositoryGoblinType


class RepositoryBundleError(ValueError):
    """Raised when a repository upload bundle cannot be accepted."""


@dataclass(frozen=True)
class RepositoryBundleLimits:
    """Upload bundle limits enforced before parsing executable source."""

    max_bundle_bytes: int = 5 * 1024 * 1024
    max_source_bytes: int = 1024 * 1024
    max_requirements_bytes: int = 64 * 1024
    max_files: int = 50


class RepositoryBundleFile(BaseModel):
    """Safe metadata for one uploaded bundle member."""

    path: str
    size: int
    executable: bool = False


class RepositoryBundleManifest(BaseModel):
    """Versioned manifest shipped in a Goblin Repository upload bundle."""

    schema_version: Literal[1]
    name: str
    type: RepositoryGoblinType
    entrypoint: str
    display_name: str | None = None
    description: str | None = None
    tags: list[str] = Field(default_factory=list)
    project_id: str | None = None
    image: str | None = None
    function_name: str = Field(default="run", min_length=1)
    timeout_seconds: int | None = Field(default=None, gt=0)
    max_retries: int = Field(default=0, ge=0)
    app_name: str = Field(default="app", min_length=1)
    requirements: list[str] = Field(default_factory=list)
    requirements_file: str | None = None
    port: int = Field(default=8080, gt=0)
    probe_path: str = Field(default="/hello", min_length=1)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not GOBLIN_KIND_PATTERN.match(value):
            raise ValueError("name must use lowercase letters, digits, dots, or dashes")
        return value

    @field_validator("entrypoint", "requirements_file")
    @classmethod
    def validate_bundle_path(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return validate_safe_bundle_path(value)

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, value: list[str]) -> list[str]:
        tags: list[str] = []
        for tag in value:
            cleaned = tag.strip().lower()
            if not cleaned:
                continue
            if not GOBLIN_KIND_PATTERN.match(cleaned):
                raise ValueError("tags must use lowercase letters, digits, dots, or dashes")
            if cleaned not in tags:
                tags.append(cleaned)
        return tags


class RepositoryBundlePreview(BaseModel):
    """Validated upload bundle preview and normalized repository submit payload."""

    manifest: RepositoryBundleManifest
    files: list[RepositoryBundleFile]
    source_preview: str
    requirements: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    submit_payload: dict[str, Any]


def parse_repository_bundle(
    data: bytes,
    *,
    limits: RepositoryBundleLimits | None = None,
) -> RepositoryBundlePreview:
    """Parse a v1 zip bundle into a repository submission payload."""
    limits = limits or RepositoryBundleLimits()
    if not data:
        raise RepositoryBundleError("bundle is empty")
    if len(data) > limits.max_bundle_bytes:
        raise RepositoryBundleError(
            f"bundle is too large: {len(data)} bytes exceeds {limits.max_bundle_bytes}"
        )
    try:
        archive = zipfile.ZipFile(BytesIO(data))
    except zipfile.BadZipFile as error:
        raise RepositoryBundleError("bundle must be a valid zip file") from error

    with archive:
        infos = [info for info in archive.infolist() if not info.is_dir()]
        if len(infos) > limits.max_files:
            raise RepositoryBundleError(
                f"bundle has too many files: {len(infos)} exceeds {limits.max_files}"
            )
        paths: dict[str, zipfile.ZipInfo] = {}
        total_size = 0
        for info in infos:
            path = validate_safe_bundle_path(info.filename)
            if path in paths:
                raise RepositoryBundleError(f"bundle contains duplicate file path: {path}")
            total_size += info.file_size
            if total_size > limits.max_bundle_bytes:
                raise RepositoryBundleError(
                    f"bundle uncompressed content exceeds {limits.max_bundle_bytes} bytes"
                )
            paths[path] = info

        if "goblin-repository.json" not in paths:
            raise RepositoryBundleError("bundle must contain goblin-repository.json at the root")
        manifest = _read_manifest(archive, paths["goblin-repository.json"])

        entry_info = paths.get(manifest.entrypoint)
        if entry_info is None:
            raise RepositoryBundleError(f"bundle entrypoint is missing: {manifest.entrypoint}")
        if entry_info.file_size > limits.max_source_bytes:
            raise RepositoryBundleError(
                f"entrypoint is too large: {entry_info.file_size} exceeds {limits.max_source_bytes}"
            )
        source = _read_text_file(archive, entry_info, label="entrypoint")

        requirements = list(manifest.requirements)
        if manifest.requirements_file:
            requirements_info = paths.get(manifest.requirements_file)
            if requirements_info is None:
                raise RepositoryBundleError(
                    f"requirements_file is missing: {manifest.requirements_file}"
                )
            if requirements_info.file_size > limits.max_requirements_bytes:
                raise RepositoryBundleError(
                    "requirements_file is too large: "
                    f"{requirements_info.file_size} exceeds {limits.max_requirements_bytes}"
                )
            requirements.extend(_read_requirements(archive, requirements_info))
        requirements = _dedupe_requirements(requirements)

        files = [
            RepositoryBundleFile(
                path=path,
                size=info.file_size,
                executable=path == manifest.entrypoint,
            )
            for path, info in sorted(paths.items())
        ]
        ignored = [
            file.path
            for file in files
            if file.path
            not in {
                "goblin-repository.json",
                manifest.entrypoint,
                manifest.requirements_file or "",
            }
        ]
        warnings = [
            "extra files are shown for review but not executed in bundle schema v1"
        ] if ignored else []
        payload = _submit_payload(manifest, source, requirements, files)
        return RepositoryBundlePreview(
            manifest=manifest,
            files=files,
            source_preview=source[:2000],
            requirements=requirements,
            warnings=warnings,
            submit_payload=payload,
        )


def validate_safe_bundle_path(value: str) -> str:
    """Normalize a zip member path and reject traversal or absolute paths."""
    normalized = value.replace("\\", "/").strip()
    if not normalized:
        raise ValueError("bundle paths cannot be empty")
    path = PurePosixPath(normalized)
    if path.is_absolute():
        raise ValueError(f"bundle paths must be relative: {value}")
    if any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"bundle path is not safe: {value}")
    return path.as_posix()


def _read_manifest(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> RepositoryBundleManifest:
    try:
        raw = _read_text_file(archive, info, label="manifest")
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        raise RepositoryBundleError("goblin-repository.json must be valid JSON") from error
    if not isinstance(payload, dict):
        raise RepositoryBundleError("goblin-repository.json must contain a JSON object")
    try:
        return RepositoryBundleManifest.model_validate(payload)
    except ValidationError as error:
        raise RepositoryBundleError(str(error)) from error


def _read_text_file(archive: zipfile.ZipFile, info: zipfile.ZipInfo, *, label: str) -> str:
    try:
        data = archive.read(info)
        return data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise RepositoryBundleError(f"{label} must be UTF-8 text") from error


def _read_requirements(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> list[str]:
    text = _read_text_file(archive, info, label="requirements_file")
    requirements = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        requirements.append(stripped)
    return requirements


def _dedupe_requirements(requirements: list[str]) -> list[str]:
    deduped: list[str] = []
    for requirement in requirements:
        cleaned = requirement.strip()
        if cleaned and cleaned not in deduped:
            deduped.append(cleaned)
    return deduped


def _submit_payload(
    manifest: RepositoryBundleManifest,
    source: str,
    requirements: list[str],
    files: list[RepositoryBundleFile],
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": manifest.name,
        "type": manifest.type,
        "source": source,
        "display_name": manifest.display_name,
        "description": manifest.description,
        "tags": manifest.tags,
        "project_id": manifest.project_id,
        "image": manifest.image,
        "metadata": {
            "bundle_schema_version": manifest.schema_version,
            "bundle_entrypoint": manifest.entrypoint,
            "bundle_files": [file.model_dump() for file in files],
        },
    }
    if manifest.type == "notebook_function":
        payload.update(
            {
                "function_name": manifest.function_name,
                "timeout_seconds": manifest.timeout_seconds,
                "max_retries": manifest.max_retries,
            }
        )
    else:
        payload.update(
            {
                "app_name": manifest.app_name,
                "requirements": requirements,
                "port": manifest.port,
                "probe_path": manifest.probe_path,
            }
        )
    return payload
