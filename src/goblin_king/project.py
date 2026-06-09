"""Project integration settings for reusable Goblin King adoption."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError


class ProjectSettingsError(ValueError):
    """Raised when project integration settings cannot be loaded."""


class ProjectSettings(BaseModel):
    """Describe registry, worker image, and API settings for an adopting project."""

    registries: list[Path] = Field(default_factory=lambda: [Path("examples/goblins.json")])
    entry_points: bool = True
    images: Path = Path("goblin-images.json")
    api_settings: Path = Path("goblin-king-api.json")

    @classmethod
    def from_path(cls, path: str | Path) -> ProjectSettings:
        """Load project settings from JSON and resolve paths relative to that file."""
        settings_path = Path(path)
        try:
            payload = json.loads(settings_path.read_text(encoding="utf-8"))
        except FileNotFoundError as error:
            raise ProjectSettingsError(
                f"project settings file not found: {settings_path}"
            ) from error
        except json.JSONDecodeError as error:
            raise ProjectSettingsError(
                f"project settings file is not valid JSON: {settings_path}"
            ) from error
        try:
            settings = cls.model_validate(payload)
        except ValidationError as error:
            raise ProjectSettingsError(str(error)) from error
        return settings.resolve_relative_to(settings_path.resolve().parent)

    def resolve_relative_to(self, root: Path) -> ProjectSettings:
        """Resolve path fields relative to the settings file directory."""
        return self.model_copy(
            update={
                "registries": [_resolve(root, path) for path in self.registries],
                "images": _resolve(root, self.images),
                "api_settings": _resolve(root, self.api_settings),
            }
        )


def _resolve(root: Path, path: Path) -> Path:
    """Resolve one project path without requiring it to exist."""
    if path.is_absolute():
        return path
    return (root / path).resolve()
