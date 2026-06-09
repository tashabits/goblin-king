"""Worker image configuration for Docker-backed goblin execution."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError, field_validator


class WorkerConfigError(ValueError):
    """Raised when Docker worker image configuration is missing or invalid."""


class WorkerImageDefinition(BaseModel):
    """Describe how to build and run one goblin worker image."""

    context: Path
    dockerfile: str = Field(default="Dockerfile", min_length=1)
    image: str = Field(min_length=1)

    @field_validator("context")
    @classmethod
    def validate_context(cls, value: Path) -> Path:
        """Reject empty worker build contexts before Docker commands are composed."""
        if str(value).strip() == "":
            raise ValueError("context must not be empty")
        return value


class WorkerImageDocument(BaseModel):
    """Validate the top-level worker image map shape."""

    workers: dict[str, WorkerImageDefinition] = Field(default_factory=dict)


class WorkerImageMap:
    """Load worker image build/run settings by goblin kind."""

    def __init__(self, workers: dict[str, WorkerImageDefinition], root: Path) -> None:
        self._workers = workers
        self._root = root

    @classmethod
    def from_path(cls, path: str | Path) -> WorkerImageMap:
        """Read and validate a worker image map from disk."""
        image_path = Path(path)
        try:
            payload = json.loads(image_path.read_text(encoding="utf-8"))
        except FileNotFoundError as error:
            raise WorkerConfigError(f"worker image map not found: {image_path}") from error
        except json.JSONDecodeError as error:
            raise WorkerConfigError(f"worker image map is not valid JSON: {image_path}") from error

        try:
            document = WorkerImageDocument.model_validate(payload)
        except ValidationError as error:
            raise WorkerConfigError(str(error)) from error
        return cls(document.workers, image_path.resolve().parent)

    def get(self, kind: str) -> WorkerImageDefinition:
        """Return the worker image definition for one goblin kind."""
        try:
            return self._workers[kind]
        except KeyError as error:
            available = ", ".join(sorted(self._workers)) or "<none>"
            raise WorkerConfigError(
                f"missing Docker worker image mapping for {kind!r}; available: {available}"
            ) from error

    def resolved_context(self, definition: WorkerImageDefinition) -> Path:
        """Resolve a worker build context relative to the image map file."""
        if definition.context.is_absolute():
            return definition.context
        return (self._root / definition.context).resolve()

    def items(self) -> list[tuple[str, WorkerImageDefinition]]:
        """Return worker image definitions sorted by kind for stable CLI output."""
        return [(kind, self._workers[kind]) for kind in sorted(self._workers)]
