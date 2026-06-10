"""Scheduler service for materializing due schedules and executing leased goblin jobs."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from threading import Event
from typing import Literal
from uuid import uuid4
from zoneinfo import ZoneInfo

from croniter import croniter

from goblin_king.contracts import (
    GoblinDefinition,
    GoblinResult,
    JobRecord,
    RunRecord,
    ScheduleRecord,
    utc_now,
)
from goblin_king.events import EventBus
from goblin_king.registry import GoblinRegistry
from goblin_king.resource_policies import ResourcePolicySet, policy_from_job_metadata
from goblin_king.runtime import DockerRuntime, InProcessRuntime, KubernetesRuntime, new_run_context
from goblin_king.store import SQLiteStore
from goblin_king.workers import WorkerImageMap

DEFAULT_LEASE_SECONDS = 60
DEFAULT_CLAIM_LIMIT = 10
DEFAULT_INTERVAL_SECONDS = 5
RuntimeMode = Literal["docker", "kubernetes", "in-process"]


class Scheduler:
    """Coordinate schedule materialization, job leasing, execution, and status updates."""

    def __init__(
        self,
        *,
        registry: GoblinRegistry,
        store: SQLiteStore,
        worker_id: str | None = None,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
        claim_limit: int = DEFAULT_CLAIM_LIMIT,
        runtime_mode: RuntimeMode = "docker",
        workers: WorkerImageMap | None = None,
        redis_url: str = "redis://localhost:6379/0",
        event_bus: EventBus | None = None,
        resource_policies: ResourcePolicySet | None = None,
    ) -> None:
        self.registry = registry
        self.store = store
        self.workers = workers
        self.redis_url = redis_url
        self.worker_id = worker_id or f"scheduler-{uuid4()}"
        self.lease_seconds = lease_seconds
        self.claim_limit = claim_limit
        self.runtime_mode = runtime_mode
        self.discovery_version = 1
        if runtime_mode in {"docker", "kubernetes"} and workers is None:
            raise ValueError(f"workers image map is required when runtime_mode={runtime_mode!r}")
        self.event_bus = event_bus or EventBus(store=store, redis_url=redis_url)
        self.resource_policies = resource_policies
        self.runtime = self._build_runtime()

    def reload_discovery(
        self,
        *,
        registry: GoblinRegistry,
        workers: WorkerImageMap | None = None,
    ) -> int:
        """Swap scheduler registry/image-map state after deploy-time discovery reload."""
        if self.runtime_mode in {"docker", "kubernetes"} and workers is None:
            raise ValueError(
                f"workers image map is required when runtime_mode={self.runtime_mode!r}"
            )
        self.registry = registry
        if workers is not None:
            self.workers = workers
        self.runtime = self._build_runtime()
        self.discovery_version += 1
        return self.discovery_version

    def _build_runtime(self) -> DockerRuntime | KubernetesRuntime | InProcessRuntime:
        """Build the configured runtime from the current registry/image-map bindings."""
        if self.runtime_mode == "docker":
            return DockerRuntime(
                workers=self.workers,
                redis_url=self.redis_url,
                event_bus=self.event_bus,
            )
        if self.runtime_mode == "kubernetes":
            return KubernetesRuntime(
                workers=self.workers,
                redis_url=self.redis_url,
                event_bus=self.event_bus,
            )
        return InProcessRuntime()

    def materialize_due_schedules(self, now: datetime | None = None) -> list[JobRecord]:
        """Create queued jobs for enabled schedules whose next run is due."""
        current = _ensure_utc(now or utc_now())
        materialized: list[JobRecord] = []
        for schedule in self.store.list_due_schedules(current):
            definition = self.registry.get(schedule.kind)
            job = JobRecord(
                id=str(uuid4()),
                kind=definition.kind,
                input=schedule.input,
                created_at=current,
                created_by="scheduler",
                project_id=schedule.project_id,
                status="queued",
                priority=schedule.priority,
                schedule_id=schedule.id,
                due_at=current,
                max_retries=schedule.max_retries,
                timeout_seconds=schedule.timeout_seconds,
                metadata=_job_metadata(definition),
            )
            if self.resource_policies is not None:
                try:
                    policy = self.resource_policies.effective_for(
                        schedule.kind,
                        timeout_seconds=schedule.timeout_seconds,
                        max_retries=schedule.max_retries,
                    )
                except ValueError as error:
                    self.event_bus.emit(
                        "resource_policy.rejected",
                        source="scheduler",
                        project_id=schedule.project_id,
                        schedule_id=schedule.id,
                        scheduler_id=self.worker_id,
                        payload={"kind": schedule.kind, "error": str(error)},
                    )
                    continue
                job = job.model_copy(
                    update={
                        "metadata": {**job.metadata, "resource_policy": policy.compact()},
                        "timeout_seconds": policy.timeout_seconds,
                        "max_retries": policy.max_retries or 0,
                    }
                )
            self.store.save_job(job)
            self.store.update_schedule_after_materialize(
                schedule.id,
                last_materialized_at=current,
                next_run_at=next_run_after(schedule, current),
            )
            self.event_bus.emit(
                "schedule.materialized",
                source="scheduler",
                project_id=schedule.project_id,
                job_id=job.id,
                schedule_id=schedule.id,
                scheduler_id=self.worker_id,
                payload={"kind": schedule.kind, "due_at": current.isoformat()},
            )
            materialized.append(job)
        return materialized

    def claim_due_jobs(self, now: datetime | None = None) -> list[JobRecord]:
        """Lease due queued or retrying jobs for this scheduler worker."""
        current = _ensure_utc(now or utc_now())
        jobs = self.store.claim_due_jobs(
            worker_id=self.worker_id,
            now=current,
            lease_until=current + timedelta(seconds=self.lease_seconds),
            limit=self.claim_limit,
        )
        runnable: list[JobRecord] = []
        for job in jobs:
            policy = policy_from_job_metadata(job.metadata)
            max_running = policy.concurrency.max_running if policy else None
            if max_running is not None:
                active_count = self.store.count_active_jobs(job.kind, exclude_job_id=job.id)
                if active_count >= max_running:
                    self.store.finish_job(job.id, status="queued", due_at=current)
                    self.event_bus.emit(
                        "resource_policy.concurrency_deferred",
                        source="scheduler",
                        project_id=job.project_id,
                        job_id=job.id,
                        schedule_id=job.schedule_id,
                        fanout_id=job.fanout_id,
                        scheduler_id=self.worker_id,
                        payload={
                            "kind": job.kind,
                            "max_running": max_running,
                            "active_count": active_count,
                        },
                    )
                    continue
            self.event_bus.emit(
                "job.leased",
                source="scheduler",
                project_id=job.project_id,
                job_id=job.id,
                schedule_id=job.schedule_id,
                fanout_id=job.fanout_id,
                scheduler_id=self.worker_id,
                payload={"kind": job.kind, "leased_until": job.leased_until.isoformat()},
            )
            runnable.append(job)
        return runnable

    def run_claimed_job(self, job: JobRecord, now: datetime | None = None) -> RunRecord:
        """Execute one leased job and persist both run and final job status."""
        started_at = _ensure_utc(now or utc_now())
        attempt = job.attempt_count + 1
        self.store.mark_job_running(job.id, attempt_count=attempt)
        self.event_bus.emit(
            "job.running",
            source="scheduler",
            project_id=job.project_id,
            job_id=job.id,
            schedule_id=job.schedule_id,
            fanout_id=job.fanout_id,
            scheduler_id=self.worker_id,
            payload={"kind": job.kind, "attempt": attempt},
        )

        if self.runtime_mode in {"docker", "kubernetes"}:
            definition = self.registry.get(job.kind)
            entrypoint = None
        else:
            definition, entrypoint = self.registry.resolve(job.kind)
        context = new_run_context(job.id, job.kind, attempt)
        resource_policy = policy_from_job_metadata(job.metadata)
        source_metadata = {
            key: value
            for key, value in {
                "goblin_source": job.metadata.get("goblin_source"),
                "goblin_definition": job.metadata.get("goblin_definition"),
            }.items()
            if value is not None
        }
        if job.project_id is not None:
            context = context.model_copy(
                update={"metadata": {**context.metadata, "project_id": job.project_id}}
            )
        if source_metadata:
            context = context.model_copy(
                update={"metadata": {**context.metadata, **source_metadata}}
            )
        if resource_policy is not None:
            context = context.model_copy(
                update={
                    "metadata": {
                        **context.metadata,
                        "resource_policy": resource_policy.compact(),
                    }
                }
            )
        if isinstance(self.runtime, DockerRuntime | KubernetesRuntime):
            result = self.runtime.run(
                definition,
                entrypoint,
                job.input,
                context,
                timeout_seconds=job.timeout_seconds,
                resource_policy=resource_policy,
            )
        else:
            result = self.runtime.run(definition, entrypoint, job.input, context)
        finished_at = utc_now()
        status = _status_for_result(result, started_at, finished_at, job.timeout_seconds)
        error = result.error
        if status == "timed_out" and error is None:
            error = f"job exceeded timeout_seconds={job.timeout_seconds}"
            result = GoblinResult.failed(error=error, data=result.data)

        run = RunRecord(
            id=context.run_id,
            job_id=job.id,
            kind=job.kind,
            project_id=job.project_id,
            attempt=attempt,
            status=status,
            started_at=started_at,
            finished_at=finished_at,
            result=result,
            error=error,
            timeout_seconds=job.timeout_seconds,
            max_retries=job.max_retries,
            leased_until=job.leased_until,
            resource_policy=resource_policy.compact() if resource_policy else None,
        )
        self.store.save_run(run)

        if status == "failed" and attempt <= job.max_retries:
            self.store.finish_job(
                job.id,
                status="retrying",
                last_error=error,
                due_at=started_at,
            )
            self.event_bus.emit(
                "job.retrying",
                source="scheduler",
                project_id=job.project_id,
                job_id=job.id,
                run_id=run.id,
                schedule_id=job.schedule_id,
                fanout_id=job.fanout_id,
                scheduler_id=self.worker_id,
                payload={"kind": job.kind, "attempt": attempt, "error": error},
            )
        else:
            self.store.finish_job(job.id, status=status, last_error=error)
            self.event_bus.emit(
                f"job.{status}",
                source="scheduler",
                project_id=job.project_id,
                job_id=job.id,
                run_id=run.id,
                schedule_id=job.schedule_id,
                fanout_id=job.fanout_id,
                scheduler_id=self.worker_id,
                payload={"kind": job.kind, "attempt": attempt, "error": error},
            )
        return run

    def run_once(self, now: datetime | None = None) -> list[RunRecord]:
        """Perform one deterministic scheduler pass for tests and CLI use."""
        current = _ensure_utc(now or utc_now())
        self.event_bus.heartbeat(
            owner_id=self.worker_id,
            owner_type="scheduler",
            status="running",
            payload={"runtime": self.runtime_mode},
        )
        self.materialize_due_schedules(current)
        runs: list[RunRecord] = []
        for job in self.claim_due_jobs(current):
            runs.append(self.run_claimed_job(job, current))
        return runs

    def run_loop(
        self,
        *,
        interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
        stop_event: Event | None = None,
    ) -> None:
        """Run scheduler passes until the optional stop event is set."""
        event = stop_event or Event()
        while not event.is_set():
            self.run_once()
            event.wait(interval_seconds)


def next_run_after(schedule: ScheduleRecord, now: datetime) -> datetime:
    """Calculate the next UTC run time for a schedule after the supplied timestamp."""
    timezone = ZoneInfo(schedule.timezone)
    localized_now = _ensure_utc(now).astimezone(timezone)
    next_local = croniter(schedule.cron, localized_now).get_next(datetime)
    return _ensure_utc(next_local.astimezone(UTC))


def _status_for_result(
    result: GoblinResult,
    started_at: datetime,
    finished_at: datetime,
    timeout_seconds: int | None,
) -> str:
    """Map a result envelope plus elapsed time into a persisted run/job status."""
    if timeout_seconds is not None and (finished_at - started_at).total_seconds() > timeout_seconds:
        return "timed_out"
    return "completed" if result.status == "success" else "failed"


def _ensure_utc(value: datetime) -> datetime:
    """Normalize scheduler timestamps to timezone-aware UTC values."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _job_metadata(definition: GoblinDefinition) -> dict:
    """Return source metadata for scheduler-materialized jobs."""
    metadata = getattr(definition, "metadata", {}) or {}
    return {
        "goblin_source": metadata.get("source", "registry"),
        "goblin_definition": definition.model_dump(mode="json"),
    }


def sleep_forever() -> None:  # pragma: no cover - manual helper
    """Keep a foreground process alive for debugger-driven scheduler experiments."""
    while True:
        time.sleep(3600)
