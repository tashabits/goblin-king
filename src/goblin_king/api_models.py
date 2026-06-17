"""Pydantic request and response models for the FastAPI control plane."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from goblin_king.contracts import (
    ApiTokenRecord,
    AuditLogRecord,
    EventRecord,
    JobRecord,
    LongServiceRecord,
    NotebookGoblinRecord,
    RunRecord,
)
from goblin_king.termination import RuntimeTarget
from goblin_king.validation import WorkerValidationResult


class ErrorEnvelope(BaseModel):
    """Consistent API error response shape for generated clients."""

    detail: str


class PageMeta(BaseModel):
    """Pagination metadata shared by list responses."""

    limit: int
    offset: int
    count: int


class JobListResponse(BaseModel):
    """Paginated job list response."""

    items: list[JobRecord]
    meta: PageMeta


class EventListResponse(BaseModel):
    """Paginated event list response."""

    items: list[EventRecord]
    meta: PageMeta


class EventStreamStatusResponse(BaseModel):
    """Redis Stream delivery health response for operators."""

    stream: str
    ok: bool
    length: int
    last_generated_id: str | None
    groups: list[dict[str, Any]]
    pending: int
    error: str | None = None


class AuditLogListResponse(BaseModel):
    """Paginated audit log list response."""

    items: list[AuditLogRecord]
    meta: PageMeta


class RunListResponse(BaseModel):
    """Paginated run list response."""

    items: list[RunRecord]
    meta: PageMeta


class TokenCreateResponse(BaseModel):
    """API token create response that returns the raw token only once."""

    token: ApiTokenRecord
    raw_token: str


class JobCreateRequest(BaseModel):
    """Request body for queueing one job through the API."""

    kind: str
    input: dict[str, Any] = Field(default_factory=dict)
    priority: int = 100
    project_id: str | None = None
    correlation_id: str | None = None
    max_retries: int = Field(default=0, ge=0)
    timeout_seconds: int | None = Field(default=None, gt=0)


class ScheduleCreateRequest(BaseModel):
    """Request body for creating one recurring schedule."""

    kind: str
    cron: str
    input: dict[str, Any] = Field(default_factory=dict)
    timezone: str = "UTC"
    project_id: str | None = None
    enabled: bool = True
    priority: int = 100
    max_retries: int = Field(default=0, ge=0)
    timeout_seconds: int | None = Field(default=None, gt=0)
    due_now: bool = False


class SchedulePatchRequest(BaseModel):
    """Partial request body for updating a recurring schedule."""

    cron: str | None = None
    input: dict[str, Any] | None = None
    timezone: str | None = None
    enabled: bool | None = None
    priority: int | None = None
    max_retries: int | None = Field(default=None, ge=0)
    timeout_seconds: int | None = Field(default=None, gt=0)


class UserCreateRequest(BaseModel):
    """Admin request for creating a local API user."""

    email: str
    display_name: str


class ProjectCreateRequest(BaseModel):
    """Admin request for creating a local project."""

    name: str


class TokenCreateRequest(BaseModel):
    """Admin request for creating a local API token."""

    name: str
    user_id: str
    project_id: str | None = None
    role: str = "member"


class LongServiceCreateRequest(BaseModel):
    """Request body for registering a long-running service goblin."""

    kind: str = "example.long-hello"
    image: str | None = None
    base_url: str | None = None
    probe_path: str | None = None
    project_id: str | None = None


class LongServiceProbeResponse(BaseModel):
    """Response body for a captured long-running service probe."""

    service: LongServiceRecord
    request: dict[str, Any]
    response: dict[str, Any]


class NotebookGoblinCreateRequest(BaseModel):
    """Request body for building a notebook-defined Python function goblin."""

    kind: str
    source: str = Field(min_length=1)
    function_name: str = Field(default="run", min_length=1)
    display_name: str | None = None
    image: str | None = None
    project_id: str | None = None
    timeout_seconds: int | None = Field(default=None, gt=0)
    max_retries: int = Field(default=0, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class NotebookGoblinValidateRequest(BaseModel):
    """Request body for validating one notebook-defined function goblin."""

    input: dict[str, Any] = Field(default_factory=dict)
    require_success: bool = True
    timeout_seconds: int | None = Field(default=None, gt=0)


class NotebookGoblinValidateResponse(BaseModel):
    """Validation proof for a notebook-defined function goblin."""

    goblin: NotebookGoblinRecord
    validation: WorkerValidationResult


class RuntimeCleanupRequest(BaseModel):
    """Admin request for pruning historical local runtime rows."""

    dry_run: bool = True
    project_id: str | None = None
    include_unprobed_services: bool = True


class RuntimeCleanupResponse(BaseModel):
    """Counts of runtime rows selected or deleted by an admin cleanup."""

    dry_run: bool
    deleted: bool
    counts: dict[str, int]


class ArtifactStorageStatusResponse(BaseModel):
    """Filesystem-backed artifact storage status."""

    root: str
    exists: bool
    writable: bool
    file_count: int
    total_bytes: int
    metadata_count: int


class ArtifactCleanupRequest(BaseModel):
    """Admin request for pruning files from the artifact volume."""

    dry_run: bool = True
    project_id: str | None = None
    max_age_seconds: int | None = Field(default=None, ge=0)
    max_total_bytes: int | None = Field(default=None, ge=0)


class ArtifactCleanupResponse(BaseModel):
    """Artifact cleanup counts and selected file paths."""

    dry_run: bool
    deleted: bool
    root: str
    files_selected: int
    bytes_selected: int
    files: list[str]


class ImagePromotionCreateRequest(BaseModel):
    """Admin request for planning or proving one worker image promotion."""

    kind: str
    target_image: str
    source_image: str | None = None
    actor: str = "api"
    build: bool = False
    push: bool = False
    dry_run: bool = True


class ImagePromotionUpdateRequest(BaseModel):
    """Admin request for updating promotion status and digest proof."""

    status: str = "promoted"
    digest: str | None = None
    detail: dict[str, Any] = Field(default_factory=dict)


class HelmTemplateRequest(BaseModel):
    """Admin request for recording or executing Helm render proof."""

    name: str = "goblin-king"
    release: str = "goblin-king"
    chart: str = "charts/goblin-king"
    namespace: str | None = None
    values: str | None = None
    actor: str = "api"
    execute: bool = False


class RuntimeTerminationRequest(BaseModel):
    """Admin request for hard-killing scoped runtime objects."""

    runtime: RuntimeTarget = "both"
    namespace: str | None = None


class RuntimeTerminationResponse(BaseModel):
    """Result of a scoped runtime hard-kill request."""

    target_type: str
    target_id: str
    runtime: RuntimeTarget
    killed: list[str]
    errors: list[str]
    cancelled: bool = False


class DiscoveryStatusResponse(BaseModel):
    """Current deploy-time discovery state exposed to operators."""

    active_goblin_count: int
    worker_mapped_count: int
    worker_unmapped: list[str]
    discovery_version: int
    last_successful_reload_at: datetime
    last_failed_reload_at: datetime | None = None
    last_error: str | None = None


class DiscoverySourcesResponse(BaseModel):
    """Loaded registry and worker-image sources for the active discovery version."""

    project_settings: str | None
    registry_files: list[str]
    entry_points_enabled: bool
    worker_image_map: str
    goblin_kinds: list[str]
    worker_mapped_kinds: list[str]
    worker_unmapped_kinds: list[str]
    rejected_definitions: list[str] = Field(default_factory=list)
    duplicate_kind_errors: list[str] = Field(default_factory=list)
