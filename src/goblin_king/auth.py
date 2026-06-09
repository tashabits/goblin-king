"""Local API token auth, RBAC, audit, and rate limiting helpers."""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from uuid import uuid4

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


@dataclass(frozen=True)
class Principal:
    """Authenticated API caller context."""

    user_id: str
    token_id: str
    role: str
    project_id: str | None = None
    bootstrap: bool = False

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
