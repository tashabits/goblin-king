"""Settings loader for the FastAPI control plane."""

from __future__ import annotations

import json
import os
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError


class ApiSettingsError(ValueError):
    """Raised when API settings cannot be loaded or validated."""


class ApiSettings(BaseModel):
    """Resolve settings needed by the API app and its dependencies."""

    registry: Path = Path("examples/goblins.json")
    images: Path = Path("goblin-images.json")
    db: Path = Path(".goblin-king/goblin-king.sqlite3")
    redis_url: str = "redis://localhost:6379/0"
    artifact_root: Path = Path(".goblin-king/artifacts")
    auth_token: str = Field(default="local-dev-token", min_length=1)

    @classmethod
    def from_path(cls, path: str | Path) -> ApiSettings:
        """Load API settings from JSON and resolve relative paths from that file."""
        settings_path = Path(path)
        try:
            payload = json.loads(settings_path.read_text(encoding="utf-8"))
        except FileNotFoundError as error:
            raise ApiSettingsError(f"API settings file not found: {settings_path}") from error
        except json.JSONDecodeError as error:
            raise ApiSettingsError(
                f"API settings file is not valid JSON: {settings_path}"
            ) from error

        token = os.environ.get("GOBLIN_KING_API_TOKEN")
        if token:
            payload["auth_token"] = token
        try:
            settings = cls.model_validate(payload)
        except ValidationError as error:
            raise ApiSettingsError(str(error)) from error
        return settings.resolve_relative_to(settings_path.resolve().parent)

    def resolve_relative_to(self, root: Path) -> ApiSettings:
        """Resolve path fields relative to the settings file directory."""
        return self.model_copy(
            update={
                "registry": _resolve(root, self.registry),
                "images": _resolve(root, self.images),
                "db": _resolve(root, self.db),
                "artifact_root": _resolve(root, self.artifact_root),
            }
        )


def _resolve(root: Path, path: Path) -> Path:
    """Resolve relative configuration paths without touching the filesystem."""
    if path.is_absolute():
        return path
    return (root / path).resolve()
