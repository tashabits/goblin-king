"""Local API token auth, RBAC, audit, and rate limiting helpers."""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Protocol
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
            return authenticate_oidc_token(token, oidc)
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
