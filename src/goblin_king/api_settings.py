"""Settings loader for the FastAPI control plane."""

from __future__ import annotations

import json
import os
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError


class ApiSettingsError(ValueError):
    """Raised when API settings cannot be loaded or validated."""


class OidcSettings(BaseModel):
    """OIDC/JWT validation settings for external bearer tokens."""

    enabled: bool = False
    issuer: str | None = None
    audience: str | None = None
    jwks_url: str | None = None
    clock_skew_seconds: int = Field(default=60, ge=0)
    user_claim: str = "sub"
    email_claim: str = "email"
    role_claim: str = "goblin_king_role"
    project_claim: str = "goblin_king_project_id"
    admin_roles: list[str] = Field(default_factory=lambda: ["admin"])


class ApiSettings(BaseModel):
    """Resolve settings needed by the API app and its dependencies."""

    registry: Path = Path("examples/goblins.json")
    images: Path = Path("goblin-images.json")
    db: Path = Path(".goblin-king/goblin-king.sqlite3")
    redis_url: str = "redis://localhost:6379/0"
    artifact_root: Path = Path(".goblin-king/artifacts")
    resource_policies: Path | None = Path("goblin-resource-policies.json")
    auth_token: str = Field(default="local-dev-token", min_length=1)
    bootstrap_admin_token: str = Field(default="local-dev-token", min_length=1)
    default_project_id: str | None = None
    rate_limit_per_minute: int = Field(default=120, ge=0)
    project: Path | None = None
    oidc: OidcSettings = Field(default_factory=OidcSettings)

    def model_post_init(self, __context: object) -> None:
        """Keep explicit legacy auth_token settings usable as bootstrap tokens."""
        if self.bootstrap_admin_token == "local-dev-token" and self.auth_token != "local-dev-token":
            self.bootstrap_admin_token = self.auth_token

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
            payload["bootstrap_admin_token"] = token
        bootstrap = os.environ.get("GOBLIN_KING_BOOTSTRAP_ADMIN_TOKEN")
        if bootstrap:
            payload["bootstrap_admin_token"] = bootstrap
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
                "project": _resolve(root, self.project) if self.project else None,
                "resource_policies": (
                    _resolve(root, self.resource_policies) if self.resource_policies else None
                ),
            }
        )


def _resolve(root: Path, path: Path) -> Path:
    """Resolve relative configuration paths without touching the filesystem."""
    if path.is_absolute():
        return path
    return (root / path).resolve()
