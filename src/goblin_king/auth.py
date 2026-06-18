"""Local API token auth, RBAC, audit, and rate limiting helpers."""

from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest
from uuid import uuid4

import jwt
from jwt import PyJWKClient
from jwt.exceptions import PyJWTError

from goblin_king.contracts import (
    ApiTokenRecord,
    AuditLogRecord,
    ProjectRecord,
    UserRecord,
    utc_now,
)
from goblin_king.store import SQLiteStore

_JUPYTERHUB_TOKEN_CACHE: dict[str, tuple[datetime, dict[str, Any]]] = {}


class AuthError(ValueError):
    """Raised when a request cannot be authenticated or authorized."""

    def __init__(self, message: str, *, status_code: int = 401) -> None:
        super().__init__(message)
        self.status_code = status_code


class RateLimitExceeded(ValueError):
    """Raised when a token exceeds its local route rate limit."""


class OidcConfig(Protocol):
    """Settings protocol needed for OIDC validation."""

    enabled: bool
    issuer: str | None
    audience: str | None
    jwks_url: str | None
    clock_skew_seconds: int
    user_claim: str
    email_claim: str
    role_claim: str
    project_claim: str
    admin_roles: list[str]


class JupyterHubConfig(Protocol):
    """Settings protocol needed for JupyterHub token validation."""

    enabled: bool
    api_url: str | None
    hub_url: str | None
    service_name: str
    service_prefix: str
    public_url: str | None
    service_token: str | None
    request_timeout_seconds: float
    cache_ttl_seconds: int
    allowed_users: list[str]
    allowed_groups: list[str]
    admin_groups: list[str]
    viewer_groups: list[str]
    project_groups: dict[str, str]
    default_project_id: str | None


@dataclass(frozen=True)
class Principal:
    """Authenticated API caller context."""

    user_id: str
    token_id: str
    role: str
    project_id: str | None = None
    bootstrap: bool = False
    auth_provider: str = "local"

    @property
    def is_admin(self) -> bool:
        """Return whether this principal can perform admin operations."""
        return self.role == "admin"


def generate_api_token() -> str:
    """Generate a bearer token value; only its hash should be persisted."""
    return f"gk_{secrets.token_urlsafe(32)}"


def hash_api_token(token: str) -> str:
    """Return the stable SHA-256 hash stored for an API token."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def authenticate_token(
    store: SQLiteStore,
    token: str,
    *,
    bootstrap_token: str,
    oidc: OidcConfig | None = None,
    jupyterhub: JupyterHubConfig | None = None,
) -> Principal:
    """Resolve a bearer token into a Principal or raise AuthError."""
    if token == bootstrap_token:
        return Principal(
            user_id="bootstrap-admin",
            token_id="bootstrap",
            role="admin",
            bootstrap=True,
        )
    token_record = store.get_api_token_by_hash(hash_api_token(token))
    if token_record is None:
        if oidc is not None and oidc.enabled:
            try:
                return authenticate_oidc_token(token, oidc)
            except AuthError as error:
                if error.status_code >= 500 or not (
                    jupyterhub is not None and jupyterhub.enabled
                ):
                    raise
        if jupyterhub is not None and jupyterhub.enabled:
            return authenticate_jupyterhub_token(token, jupyterhub)
        raise AuthError("missing or invalid bearer token", status_code=401)
    user = store.get_user(token_record.user_id)
    if user is None or user.disabled:
        raise AuthError("user is disabled or missing", status_code=403)
    return Principal(
        user_id=token_record.user_id,
        token_id=token_record.id,
        role=token_record.role,
        project_id=token_record.project_id,
    )


def authenticate_oidc_token(token: str, oidc: OidcConfig) -> Principal:
    """Validate an OIDC/JWT bearer token and map claims into a Principal."""
    claims = _decode_oidc_jwt(token, oidc)
    user_id = _claim_as_string(claims, oidc.user_claim)
    if not user_id:
        raise AuthError(f"OIDC token missing required claim: {oidc.user_claim}", status_code=401)
    role = _role_from_claims(claims, oidc)
    project_id = _claim_as_string(claims, oidc.project_claim)
    return Principal(
        user_id=user_id,
        token_id=f"oidc:{user_id}",
        role=role,
        project_id=project_id,
        auth_provider="oidc",
    )


def authenticate_jupyterhub_token(token: str, jupyterhub: JupyterHubConfig) -> Principal:
    """Validate a JupyterHub user API token and map it into a Principal."""
    model = _identify_jupyterhub_token(token, jupyterhub)
    user = _jupyterhub_user_model(model)
    user_id = user.get("name")
    if not isinstance(user_id, str) or not user_id:
        raise AuthError("JupyterHub token response missing user name", status_code=401)
    groups = _jupyterhub_groups(user)
    if not _jupyterhub_user_allowed(user_id, groups, jupyterhub):
        raise AuthError("JupyterHub user is not authorized", status_code=403)
    return Principal(
        user_id=user_id,
        token_id=f"jupyterhub:{hash_api_token(token)[:16]}",
        role=_jupyterhub_role(user, groups, jupyterhub),
        project_id=_jupyterhub_project_id(groups, jupyterhub),
        auth_provider="jupyterhub",
    )


def _identify_jupyterhub_token(token: str, jupyterhub: JupyterHubConfig) -> dict[str, Any]:
    """Ask the Hub to identify one bearer token without persisting it locally."""
    if not jupyterhub.api_url:
        raise AuthError("JupyterHub api_url is required", status_code=500)
    if not jupyterhub.service_token:
        raise AuthError("JupyterHub service token is required", status_code=500)
    cache_key = hash_api_token(token)
    if jupyterhub.cache_ttl_seconds > 0:
        cached = _JUPYTERHUB_TOKEN_CACHE.get(cache_key)
        if cached and utc_now() - cached[0] < timedelta(seconds=jupyterhub.cache_ttl_seconds):
            return cached[1]
    api_url = jupyterhub.api_url.rstrip("/")
    model = _request_jupyterhub_json(
        f"{api_url}/user",
        token=token,
        timeout_seconds=jupyterhub.request_timeout_seconds,
        invalid_token_message="missing or invalid JupyterHub bearer token",
    )
    user = _jupyterhub_user_model(model)
    user_id = user.get("name")
    if isinstance(user_id, str) and user_id:
        user_detail = _request_jupyterhub_json(
            f"{api_url}/users/{urlparse.quote(user_id, safe='')}",
            token=jupyterhub.service_token,
            timeout_seconds=jupyterhub.request_timeout_seconds,
            invalid_token_message="JupyterHub service token is invalid",
            permission_message="JupyterHub service token lacks user/group permission",
        )
        user = {**user, **user_detail}
    if jupyterhub.cache_ttl_seconds > 0:
        _JUPYTERHUB_TOKEN_CACHE[cache_key] = (utc_now(), user)
    return user


def _request_jupyterhub_json(
    url: str,
    *,
    token: str,
    timeout_seconds: float,
    invalid_token_message: str,
    permission_message: str | None = None,
) -> dict[str, Any]:
    """Read one Hub JSON API resource with token-safe error handling."""
    request = urlrequest.Request(
        url,
        headers={
            "Accept": "application/json",
            "Authorization": f"token {token}",
        },
    )
    try:
        with urlrequest.urlopen(request, timeout=timeout_seconds) as response:
            model = json.loads(response.read().decode("utf-8"))
            if not isinstance(model, dict):
                raise AuthError("JupyterHub token response was not an object", status_code=503)
            return model
    except urlerror.HTTPError as error:
        if error.code in {401, 404}:
            raise AuthError(
                invalid_token_message,
                status_code=401,
            ) from error
        if error.code == 403 and permission_message is not None:
            raise AuthError(permission_message, status_code=500) from error
        if error.code == 403:
            raise AuthError(invalid_token_message, status_code=401) from error
        raise AuthError("JupyterHub token validation failed", status_code=503) from error
    except (OSError, urlerror.URLError, json.JSONDecodeError) as error:
        raise AuthError(
            "JupyterHub is unavailable for token validation",
            status_code=503,
        ) from error


def _jupyterhub_user_model(model: dict[str, Any]) -> dict[str, Any]:
    """Return the user portion of a Hub token-identification response."""
    user = model.get("user")
    if isinstance(user, dict):
        model = user
    kind = model.get("kind")
    if kind is not None and kind != "user":
        raise AuthError("JupyterHub token does not belong to a user", status_code=403)
    return model


def _jupyterhub_groups(model: dict[str, Any]) -> list[str]:
    """Normalize Hub group models or names into string group names."""
    groups = model.get("groups")
    if not isinstance(groups, list):
        return []
    names: list[str] = []
    for item in groups:
        if isinstance(item, str):
            names.append(item)
        elif isinstance(item, dict) and isinstance(item.get("name"), str):
            names.append(item["name"])
    return names


def _jupyterhub_user_allowed(
    user_id: str,
    groups: list[str],
    jupyterhub: JupyterHubConfig,
) -> bool:
    """Apply optional Hub user and group allowlists."""
    if not jupyterhub.allowed_users and not jupyterhub.allowed_groups:
        return True
    return user_id in jupyterhub.allowed_users or bool(
        set(groups).intersection(jupyterhub.allowed_groups)
    )


def _jupyterhub_role(
    model: dict[str, Any],
    groups: list[str],
    jupyterhub: JupyterHubConfig,
) -> str:
    """Map Hub admin status and groups onto Goblin King roles."""
    if model.get("admin") is True or set(groups).intersection(jupyterhub.admin_groups):
        return "admin"
    if set(groups).intersection(jupyterhub.viewer_groups):
        return "viewer"
    return "member"


def _jupyterhub_project_id(
    groups: list[str],
    jupyterhub: JupyterHubConfig,
) -> str | None:
    """Map the first matching Hub group to a Goblin King project scope."""
    for group in groups:
        project_id = jupyterhub.project_groups.get(group)
        if project_id:
            return project_id
    return jupyterhub.default_project_id


def _decode_oidc_jwt(token: str, oidc: OidcConfig) -> dict[str, Any]:
    """Decode and validate one JWT using the configured OIDC issuer metadata."""
    if not oidc.issuer or not oidc.audience or not oidc.jwks_url:
        raise AuthError("OIDC issuer, audience, and JWKS URL are required", status_code=500)
    try:
        signing_key = PyJWKClient(oidc.jwks_url).get_signing_key_from_jwt(token)
        return jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256", "ES256"],
            audience=oidc.audience,
            issuer=oidc.issuer,
            leeway=oidc.clock_skew_seconds,
        )
    except PyJWTError as error:
        raise AuthError(
            f"missing or invalid OIDC bearer token: {error}",
            status_code=401,
        ) from error


def _role_from_claims(claims: dict[str, Any], oidc: OidcConfig) -> str:
    """Map configured role claims onto Goblin King local roles."""
    values = _claim_values(claims.get(oidc.role_claim))
    if any(value in oidc.admin_roles for value in values):
        return "admin"
    if "viewer" in values:
        return "viewer"
    return "member"


def _claim_values(value: Any) -> list[str]:
    """Normalize string/list JWT claims into a list of strings."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return []


def _claim_as_string(claims: dict[str, Any], key: str) -> str | None:
    """Return one string claim value when present."""
    value = claims.get(key)
    return value if isinstance(value, str) and value else None


def require_admin(principal: Principal) -> None:
    """Require an admin principal for management operations."""
    if not principal.is_admin:
        raise AuthError("admin role required", status_code=403)


def require_project_access(principal: Principal, project_id: str | None) -> None:
    """Require that a scoped principal only accesses its own project."""
    if principal.is_admin:
        return
    if principal.project_id is None or project_id != principal.project_id:
        raise AuthError("project access denied", status_code=403)


def create_user(store: SQLiteStore, *, email: str, display_name: str) -> UserRecord:
    """Create a local user record."""
    user = UserRecord(
        id=str(uuid4()),
        email=email,
        display_name=display_name,
        created_at=utc_now(),
    )
    store.save_user(user)
    return user


def create_project(store: SQLiteStore, *, name: str) -> ProjectRecord:
    """Create a local project record."""
    project = ProjectRecord(id=str(uuid4()), name=name, created_at=utc_now())
    store.save_project(project)
    return project


def create_api_token(
    store: SQLiteStore,
    *,
    name: str,
    user_id: str,
    project_id: str | None,
    role: str,
) -> tuple[ApiTokenRecord, str]:
    """Create a hashed token row and return the raw token once."""
    raw_token = generate_api_token()
    token = ApiTokenRecord(
        id=str(uuid4()),
        name=name,
        token_hash=hash_api_token(raw_token),
        created_at=utc_now(),
        user_id=user_id,
        project_id=project_id,
        role=role,
    )
    store.save_api_token(token)
    return token, raw_token


def audit(
    store: SQLiteStore,
    *,
    action: str,
    outcome: str,
    principal: Principal | None = None,
    project_id: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    """Persist one audit log record."""
    store.save_audit_log(
        AuditLogRecord(
            id=str(uuid4()),
            created_at=utc_now(),
            action=action,
            outcome=outcome,
            user_id=principal.user_id if principal else None,
            token_id=principal.token_id if principal else None,
            project_id=project_id,
            resource_type=resource_type,
            resource_id=resource_id,
            detail=detail or {},
        )
    )


def check_rate_limit(
    store: SQLiteStore,
    *,
    principal: Principal,
    route: str,
    max_per_minute: int,
) -> None:
    """Increment and enforce one local per-token route rate limit."""
    if max_per_minute <= 0:
        return
    now = utc_now()
    key = f"{principal.token_id}:{route}"
    existing = store.increment_rate_limit(
        key=key,
        window_started_at=now,
        reset_existing=False,
    )
    reset = now - existing.window_started_at >= timedelta(minutes=1)
    if reset:
        existing = store.increment_rate_limit(
            key=key,
            window_started_at=now,
            reset_existing=True,
        )
    if existing.count > max_per_minute:
        audit(
            store,
            action="rate_limit.denied",
            outcome="denied",
            principal=principal,
            detail={"route": route, "limit": max_per_minute, "count": existing.count},
        )
        raise RateLimitExceeded("rate limit exceeded")
