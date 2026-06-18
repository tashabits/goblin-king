"""Settings loader for the FastAPI control plane."""

from __future__ import annotations

import json
import os
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from goblin_king.jsonio import read_json_file


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


class JupyterHubSettings(BaseModel):
    """JupyterHub-backed bearer token validation settings."""

    enabled: bool = False
    api_url: str | None = None
    hub_url: str | None = None
    service_name: str = "goblin-king"
    service_prefix: str = "/services/goblin-king/"
    public_url: str | None = None
    service_token: str | None = None
    service_token_env: str = "GOBLIN_KING_JUPYTERHUB_SERVICE_TOKEN"
    request_timeout_seconds: float = Field(default=5.0, gt=0)
    cache_ttl_seconds: int = Field(default=60, ge=0)
    allowed_users: list[str] = Field(default_factory=list)
    allowed_groups: list[str] = Field(default_factory=list)
    admin_groups: list[str] = Field(default_factory=list)
    viewer_groups: list[str] = Field(default_factory=list)
    project_groups: dict[str, str] = Field(default_factory=dict)
    default_project_id: str | None = None


class RepositorySettings(BaseModel):
    """Optional repository service discovery settings."""

    enabled: bool = False
    url: str | None = None


class ApiSettings(BaseModel):
    """Resolve settings needed by the API app and its dependencies."""

    registry: Path = Path("examples/goblins.json")
    images: Path = Path("goblin-images.json")
    db: Path = Path(".goblin-king/goblin-king.sqlite3")
    redis_url: str = "redis://localhost:6379/0"
    artifact_root: Path = Path(".goblin-king/artifacts")
    resource_policies: Path | None = Path("goblin-resource-policies.json")
    notebook_function_image: str = "goblin-king-notebook-python-function:local"
    notebook_service_image: str = "goblin-king-notebook-asgi-service:local"
    notebook_service_runtime: str = "auto"
    auth_token: str = Field(default="local-dev-token", min_length=1)
    bootstrap_admin_token: str = Field(default="local-dev-token", min_length=1)
    default_project_id: str | None = None
    rate_limit_per_minute: int = Field(default=120, ge=0)
    project: Path | None = None
    oidc: OidcSettings = Field(default_factory=OidcSettings)
    jupyterhub: JupyterHubSettings = Field(default_factory=JupyterHubSettings)
    repository: RepositorySettings = Field(default_factory=RepositorySettings)

    def model_post_init(self, __context: object) -> None:
        """Keep explicit legacy auth_token settings usable as bootstrap tokens."""
        if self.bootstrap_admin_token == "local-dev-token" and self.auth_token != "local-dev-token":
            self.bootstrap_admin_token = self.auth_token

    @classmethod
    def from_path(cls, path: str | Path) -> ApiSettings:
        """Load API settings from JSON and resolve relative paths from that file."""
        settings_path = Path(path)
        try:
            payload = read_json_file(settings_path)
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
        notebook_function_image = os.environ.get("GOBLIN_KING_NOTEBOOK_FUNCTION_IMAGE")
        if notebook_function_image:
            payload["notebook_function_image"] = notebook_function_image
        notebook_service_image = os.environ.get("GOBLIN_KING_NOTEBOOK_SERVICE_IMAGE")
        if notebook_service_image:
            payload["notebook_service_image"] = notebook_service_image
        notebook_service_runtime = os.environ.get("GOBLIN_KING_NOTEBOOK_SERVICE_RUNTIME")
        if notebook_service_runtime:
            payload["notebook_service_runtime"] = notebook_service_runtime
        repository_enabled = os.environ.get("GOBLIN_KING_REPOSITORY_ENABLED")
        if repository_enabled is not None:
            repository_payload = _ensure_dict(payload, "repository")
            repository_payload["enabled"] = _env_bool(repository_enabled)
        repository_url = os.environ.get("GOBLIN_KING_REPOSITORY_URL")
        if repository_url:
            repository_payload = _ensure_dict(payload, "repository")
            repository_payload["url"] = repository_url
        hub_token = os.environ.get("GOBLIN_KING_JUPYTERHUB_SERVICE_TOKEN")
        if hub_token:
            hub_payload = payload.setdefault("jupyterhub", {})
            if isinstance(hub_payload, dict):
                hub_payload["service_token"] = hub_token
        try:
            settings = cls.model_validate(payload)
        except ValidationError as error:
            raise ApiSettingsError(str(error)) from error
        if (
            settings.jupyterhub.enabled
            and settings.jupyterhub.service_token is None
            and settings.jupyterhub.service_token_env
        ):
            env_token = os.environ.get(settings.jupyterhub.service_token_env)
            if env_token:
                settings = settings.model_copy(
                    update={
                        "jupyterhub": settings.jupyterhub.model_copy(
                            update={"service_token": env_token}
                        )
                    }
                )
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


def _ensure_dict(payload: dict[str, object], key: str) -> dict[str, object]:
    """Return a mutable mapping field, replacing invalid values."""
    value = payload.get(key)
    if not isinstance(value, dict):
        value = {}
        payload[key] = value
    return value


def _env_bool(value: str) -> bool:
    """Parse common deployment boolean strings."""
    return value.strip().lower() in {"1", "true", "yes", "on"}
