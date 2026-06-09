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


class RunRecord(BaseModel):
    """Capture one execution attempt for a job."""

    id: str
    job_id: str
    kind: str
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
