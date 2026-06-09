"""Typed contracts shared by goblin authors, runtimes, persistence, and CLI callers."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

GOBLIN_KIND_PATTERN = re.compile(r"^[a-z0-9][a-z0-9]*(?:[.-][a-z0-9][a-z0-9]*)*$")
JobStatus = Literal[
    "queued",
    "leased",
    "running",
    "completed",
    "failed",
    "retrying",
    "timed_out",
    "cancelled",
]
RunStatus = Literal["running", "completed", "failed", "timed_out"]
EventSource = Literal["api", "scheduler", "runtime", "worker", "cli"]
HeartbeatOwnerType = Literal["scheduler", "worker"]
PrincipalRole = Literal["admin", "member", "viewer"]
LongServiceStatus = Literal["registered", "running", "failed", "stopped"]
ImagePromotionStatus = Literal["planned", "built", "pushed", "promoted", "failed"]
DeploymentRecordStatus = Literal["planned", "rendered", "dry_run", "applied", "failed"]


def utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp for persisted records and result metadata."""
    return datetime.now(UTC)


class ArtifactRecord(BaseModel):
    """Describe an artifact produced by a goblin without owning its bytes in Phase 1."""

    name: str = Field(min_length=1)
    uri: str = Field(min_length=1)
    media_type: str | None = None


class HandoffRecord(BaseModel):
    """Describe structured follow-up work or storage payloads emitted by a goblin."""

    kind: str = Field(min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)


class GoblinDefinition(BaseModel):
    """Define how the registry resolves and executes one goblin kind."""

    kind: str
    display_name: str = Field(min_length=1)
    module: str = Field(min_length=1)
    entrypoint: str = Field(default="run", min_length=1)
    timeout_seconds: int | None = Field(default=None, gt=0)
    max_retries: int | None = Field(default=None, ge=0)

    @field_validator("kind")
    @classmethod
    def validate_kind(cls, value: str) -> str:
        """Reject ambiguous goblin kinds before they enter registries or records."""
        if not GOBLIN_KIND_PATTERN.match(value):
            raise ValueError("kind must use lowercase letters, digits, dots, or dashes")
        return value


class GoblinContext(BaseModel):
    """Provide one run's execution context to goblin code."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    run_id: str
    artifact_root: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class GoblinResult(BaseModel):
    """Represent the structured result envelope returned by every goblin execution."""

    status: Literal["success", "failed"]
    data: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[ArtifactRecord] = Field(default_factory=list)
    metrics: dict[str, int | float | str | bool | None] = Field(default_factory=dict)
    handoff: list[HandoffRecord] = Field(default_factory=list)
    error: str | None = None

    @classmethod
    def ok(
        cls,
        *,
        data: dict[str, Any] | None = None,
        artifacts: list[ArtifactRecord | dict[str, Any]] | None = None,
        metrics: dict[str, int | float | str | bool | None] | None = None,
        handoff: list[HandoffRecord | dict[str, Any]] | None = None,
    ) -> GoblinResult:
        """Build a successful goblin result with predictable default containers."""
        return cls(
            status="success",
            data=data or {},
            artifacts=artifacts or [],
            metrics=metrics or {},
            handoff=handoff or [],
            error=None,
        )

    @classmethod
    def failed(
        cls,
        *,
        error: str,
        data: dict[str, Any] | None = None,
        artifacts: list[ArtifactRecord | dict[str, Any]] | None = None,
        metrics: dict[str, int | float | str | bool | None] | None = None,
        handoff: list[HandoffRecord | dict[str, Any]] | None = None,
    ) -> GoblinResult:
        """Build a failed goblin result while preserving any partial metadata."""
        return cls(
            status="failed",
            data=data or {},
            artifacts=artifacts or [],
            metrics=metrics or {},
            handoff=handoff or [],
            error=error,
        )


class JobRecord(BaseModel):
    """Capture a submitted or scheduler-created job before and after execution."""

    id: str
    kind: str
    input: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    created_by: str = "cli"
    correlation_id: str | None = None
    project_id: str | None = None
    fanout_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    status: JobStatus = "queued"
    priority: int = 100
    schedule_id: str | None = None
    due_at: datetime | None = None
    lease_owner: str | None = None
    leased_until: datetime | None = None
    attempt_count: int = 0
    max_retries: int = 0
    timeout_seconds: int | None = None
    last_error: str | None = None


class FanoutRecord(BaseModel):
    """Capture durable metadata for a batch of fanout-created jobs."""

    id: str
    created_at: datetime
    created_by: str = "api"
    project_id: str | None = None
    correlation_id: str | None = None
    description: str | None = None


class EventRecord(BaseModel):
    """Capture one durable event for API, scheduler, runtime, or worker activity."""

    id: str
    created_at: datetime
    event_type: str = Field(min_length=1)
    source: EventSource
    project_id: str | None = None
    job_id: str | None = None
    run_id: str | None = None
    fanout_id: str | None = None
    schedule_id: str | None = None
    worker_id: str | None = None
    scheduler_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class HeartbeatRecord(BaseModel):
    """Track the latest known liveness signal for a scheduler or worker."""

    owner_id: str
    owner_type: HeartbeatOwnerType
    status: str = Field(min_length=1)
    last_seen_at: datetime
    job_id: str | None = None
    run_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class LongServiceRecord(BaseModel):
    """Track a long-running service-style goblin and its latest probe proof."""

    id: str
    kind: str
    project_id: str | None = None
    image: str | None = None
    base_url: str = Field(min_length=1)
    status: LongServiceStatus = "registered"
    created_at: datetime
    created_by: str = "api"
    last_probe_at: datetime | None = None
    last_probe_json: dict[str, Any] | None = None


class ImagePromotionRecord(BaseModel):
    """Track worker image promotion intent and proof across local deployment steps."""

    id: str
    kind: str
    source_image: str
    target_image: str
    status: ImagePromotionStatus = "planned"
    actor: str = "api"
    digest: str | None = None
    created_at: datetime
    updated_at: datetime
    detail: dict[str, Any] = Field(default_factory=dict)


class DeploymentRecord(BaseModel):
    """Track deployment orchestration proof such as Helm render and reload actions."""

    id: str
    name: str
    action: str
    status: DeploymentRecordStatus = "planned"
    actor: str = "api"
    command: list[str] = Field(default_factory=list)
    output: str | None = None
    created_at: datetime
    updated_at: datetime
    detail: dict[str, Any] = Field(default_factory=dict)


class RunRecord(BaseModel):
    """Capture one execution attempt for a job."""

    id: str
    job_id: str
    kind: str
    project_id: str | None = None
    attempt: int = 1
    status: RunStatus
    started_at: datetime
    finished_at: datetime | None = None
    result: GoblinResult | None = None
    error: str | None = None
    timeout_seconds: int | None = None
    max_retries: int = 0
    leased_until: datetime | None = None


class ScheduleRecord(BaseModel):
    """Capture a recurring cron schedule that materializes queued jobs."""

    id: str
    kind: str
    project_id: str | None = None
    input: dict[str, Any] = Field(default_factory=dict)
    cron: str
    timezone: str = "UTC"
    enabled: bool = True
    priority: int = 100
    created_at: datetime
    next_run_at: datetime
    last_materialized_at: datetime | None = None
    max_retries: int = 0
    timeout_seconds: int | None = None


class UserRecord(BaseModel):
    """Represent a local API user principal."""

    id: str
    email: str
    display_name: str
    created_at: datetime
    disabled: bool = False


class TeamRecord(BaseModel):
    """Represent a local team used for project membership."""

    id: str
    name: str
    created_at: datetime


class ProjectRecord(BaseModel):
    """Represent an owned project boundary for API resources."""

    id: str
    name: str
    created_at: datetime


class MembershipRecord(BaseModel):
    """Represent a user or team role within a project."""

    id: str
    project_id: str
    role: PrincipalRole
    user_id: str | None = None
    team_id: str | None = None
    created_at: datetime


class ApiTokenRecord(BaseModel):
    """Represent a hashed bearer token scoped to a user and optional project."""

    id: str
    name: str
    token_hash: str
    created_at: datetime
    user_id: str
    project_id: str | None = None
    role: PrincipalRole = "member"
    revoked_at: datetime | None = None


class AuditLogRecord(BaseModel):
    """Capture an authenticated security or mutation event."""

    id: str
    created_at: datetime
    action: str
    outcome: str
    user_id: str | None = None
    token_id: str | None = None
    project_id: str | None = None
    resource_type: str | None = None
    resource_id: str | None = None
    detail: dict[str, Any] = Field(default_factory=dict)


class RateLimitRecord(BaseModel):
    """Track one local per-token route window."""

    key: str
    window_started_at: datetime
    count: int = 0
