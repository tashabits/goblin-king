"""Persist scheduler job failures without leaving leased work in limbo."""

from __future__ import annotations

from datetime import datetime

from goblin_king.contracts import GoblinResult, JobRecord, RunRecord, utc_now
from goblin_king.events import EventBus
from goblin_king.resource_policies import policy_from_job_metadata
from goblin_king.runtime import new_run_context
from goblin_king.store import SQLiteStore

TERMINAL_JOB_STATUSES = {"completed", "failed", "timed_out", "cancelled"}


def record_unexpected_job_failure(
    *,
    store: SQLiteStore,
    event_bus: EventBus,
    scheduler_id: str,
    claimed_job: JobRecord,
    started_at: datetime,
    error: Exception,
) -> RunRecord:
    """Reconcile a raised job attempt into one durable terminal Run and job state."""
    current = store.get_job(claimed_job.id)
    if current is None:
        raise RuntimeError(
            f"cannot recover scheduler failure because job {claimed_job.id!r} disappeared"
        ) from error

    prior_runs = store.list_job_runs(current.id)
    if prior_runs:
        latest = max(prior_runs, key=lambda run: (run.attempt, run.finished_at))
        if current.status in TERMINAL_JOB_STATUSES:
            return latest
        if current.status != "leased" and latest.attempt >= current.attempt_count:
            store.finish_job(
                current.id,
                status=latest.status,
                last_error=latest.error,
            )
            return latest

    attempt = current.attempt_count
    if current.status == "leased":
        attempt += 1
    attempt = max(attempt, claimed_job.attempt_count + 1)
    context = new_run_context(current.id, current.kind, attempt)
    message = (
        "Scheduler job execution failed at the job boundary; the lease was released. "
        f"{type(error).__name__}: {error}"
    )
    result = GoblinResult.failed(error=message)
    resource_policy = policy_from_job_metadata(current.metadata)
    run = RunRecord(
        id=context.run_id,
        job_id=current.id,
        kind=current.kind,
        project_id=current.project_id,
        attempt=attempt,
        status="failed",
        started_at=started_at,
        finished_at=max(utc_now(), started_at),
        result=result,
        error=message,
        timeout_seconds=current.timeout_seconds,
        max_retries=current.max_retries,
        leased_until=current.leased_until,
        resource_policy=resource_policy.compact() if resource_policy else None,
    )
    store.save_run(run)
    store.finish_job(current.id, status="failed", last_error=message)
    event_bus.emit(
        "job.failed",
        source="scheduler",
        project_id=current.project_id,
        job_id=current.id,
        run_id=run.id,
        schedule_id=current.schedule_id,
        fanout_id=current.fanout_id,
        scheduler_id=scheduler_id,
        payload={
            "kind": current.kind,
            "attempt": attempt,
            "error": message,
            "scheduler_exception": True,
        },
    )
    return run
