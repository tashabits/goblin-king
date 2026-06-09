"""Shared fanout and retry behavior for API and CLI callers."""

from __future__ import annotations

from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from goblin_king.contracts import FanoutRecord, JobRecord, RunRecord, utc_now
from goblin_king.registry import GoblinRegistry
from goblin_king.store import SQLiteStore

TERMINAL_STATUSES = {"completed", "failed", "timed_out", "cancelled"}
ACTIVE_STATUSES = {"leased", "running", "retrying"}
FanoutStatus = Literal["queued", "running", "completed", "failed", "partial"]


class FanoutItem(BaseModel):
    """One queued job request inside a fanout batch."""

    kind: str
    input: dict[str, Any] = Field(default_factory=dict)
    priority: int = 100
    max_retries: int = Field(default=0, ge=0)
    timeout_seconds: int | None = Field(default=None, gt=0)


class FanoutCreateRequest(BaseModel):
    """Create request for a mixed-kind fanout batch."""

    description: str | None = None
    correlation_id: str | None = None
    items: list[FanoutItem] = Field(min_length=1)


class RetryCreateRequest(BaseModel):
    """Create request for a fresh retry job copied from a terminal source job."""

    reason: str | None = None
    input: dict[str, Any] | None = None
    priority: int | None = None
    max_retries: int | None = Field(default=None, ge=0)
    timeout_seconds: int | None = Field(default=None, gt=0)


class FanoutDetail(BaseModel):
    """Read model for fanout metadata, derived status, child jobs, and runs."""

    fanout: FanoutRecord
    status: FanoutStatus
    counts: dict[str, int]
    jobs: list[JobRecord]
    runs: list[RunRecord] = Field(default_factory=list)


def create_fanout(
    *,
    store: SQLiteStore,
    registry: GoblinRegistry,
    request: FanoutCreateRequest,
    created_by: str,
) -> FanoutDetail:
    """Validate all fanout items, then create the batch and queued child jobs."""
    definitions = [registry.get(item.kind) for item in request.items]
    now = utc_now()
    fanout = FanoutRecord(
        id=str(uuid4()),
        created_at=now,
        created_by=created_by,
        correlation_id=request.correlation_id or str(uuid4()),
        description=request.description,
    )
    jobs = [
        JobRecord(
            id=str(uuid4()),
            kind=definition.kind,
            input=item.input,
            created_at=now,
            created_by=created_by,
            correlation_id=fanout.correlation_id,
            fanout_id=fanout.id,
            status="queued",
            priority=item.priority,
            due_at=now,
            max_retries=item.max_retries,
            timeout_seconds=item.timeout_seconds,
            metadata={
                "fanout_item_index": index,
                "fanout_item": item.model_dump(mode="json"),
            },
        )
        for index, (definition, item) in enumerate(zip(definitions, request.items, strict=True))
    ]
    store.save_fanout(fanout)
    for job in jobs:
        store.save_job(job)
    return fanout_detail(store, fanout.id)


def retry_job(
    *,
    store: SQLiteStore,
    job_id: str,
    request: RetryCreateRequest,
    created_by: str,
) -> JobRecord:
    """Create a fresh queued retry job from one terminal source job."""
    source = store.get_job(job_id)
    if source is None:
        raise KeyError(job_id)
    if source.status not in TERMINAL_STATUSES:
        raise ValueError(f"job is not terminal: {source.status}")
    retry = JobRecord(
        id=str(uuid4()),
        kind=source.kind,
        input=source.input if request.input is None else request.input,
        created_at=utc_now(),
        created_by=created_by,
        correlation_id=source.correlation_id,
        fanout_id=source.fanout_id,
        status="queued",
        priority=source.priority if request.priority is None else request.priority,
        due_at=utc_now(),
        max_retries=source.max_retries if request.max_retries is None else request.max_retries,
        timeout_seconds=(
            source.timeout_seconds if request.timeout_seconds is None else request.timeout_seconds
        ),
        metadata={
            **source.metadata,
            "retry": {
                "source_job_id": source.id,
                "source_status": source.status,
                "reason": request.reason,
            },
        },
    )
    store.save_job(retry)
    return retry


def fanout_detail(store: SQLiteStore, fanout_id: str) -> FanoutDetail:
    """Return one fanout detail with derived status and child runs."""
    fanout = store.get_fanout(fanout_id)
    if fanout is None:
        raise KeyError(fanout_id)
    jobs = store.list_fanout_jobs(fanout_id)
    runs = [run for job in jobs for run in store.list_job_runs(job.id)]
    return FanoutDetail(
        fanout=fanout,
        status=derive_fanout_status(jobs),
        counts=_counts(jobs),
        jobs=jobs,
        runs=runs,
    )


def list_fanout_details(store: SQLiteStore) -> list[FanoutDetail]:
    """Return all fanouts with derived status and child jobs."""
    return [fanout_detail(store, fanout.id) for fanout in store.list_fanouts()]


def derive_fanout_status(jobs: list[JobRecord]) -> FanoutStatus:
    """Derive fanout status from child job statuses."""
    if not jobs:
        return "queued"
    statuses = [job.status for job in jobs]
    if all(status == "completed" for status in statuses):
        return "completed"
    if all(status in TERMINAL_STATUSES for status in statuses):
        if any(status == "completed" for status in statuses):
            return "partial"
        return "failed"
    if any(status == "completed" for status in statuses) and any(
        status in {"failed", "timed_out", "cancelled"} for status in statuses
    ):
        return "partial"
    if any(status in ACTIVE_STATUSES for status in statuses):
        return "running"
    return "queued"


def _counts(jobs: list[JobRecord]) -> dict[str, int]:
    """Count child jobs by status for fanout read models."""
    counts: dict[str, int] = {}
    for job in jobs:
        counts[job.status] = counts.get(job.status, 0) + 1
    counts["total"] = len(jobs)
    return counts
