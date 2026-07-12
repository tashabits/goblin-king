"""Scheduler service for materializing due schedules and executing leased goblin jobs."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event
from typing import Literal
from uuid import uuid4
from zoneinfo import ZoneInfo

from croniter import croniter

from goblin_king.auth import audit
from goblin_king.causal_time import causally_after
from goblin_king.contracts import (
    GoblinResult,
    JobRecord,
    RunRecord,
    ScheduleRecord,
    utc_now,
)
from goblin_king.events import EventBus
from goblin_king.kubernetes_runtime_settings import KubernetesRuntimeSettings
from goblin_king.metadata import goblin_job_metadata
from goblin_king.notebooks import (
    notebook_definition,
    notebook_validation_identity,
    notebook_worker_input,
    notebook_worker_map,
)
from goblin_king.registry import GoblinRegistry, RegistryError
from goblin_king.resource_policies import ResourcePolicySet, policy_from_job_metadata
from goblin_king.runtime import DockerRuntime, InProcessRuntime, KubernetesRuntime, new_run_context
from goblin_king.scheduler_failures import record_unexpected_job_failure
from goblin_king.store import SQLiteStore
from goblin_king.validation import (
    VALIDATOR_VERSION,
    format_validation_gate_error,
    inspect_image_identity,
    kubernetes_image_identity,
    validate_workers,
    validation_record,
)
from goblin_king.versions import GOBLIN_CONTAINER_CONTRACT_VERSION
from goblin_king.workers import WorkerConfigError, WorkerImageMap

DEFAULT_LEASE_SECONDS = 60
DEFAULT_CLAIM_LIMIT = 10
DEFAULT_INTERVAL_SECONDS = 5


class JobAttemptSuperseded(RuntimeError):
    """Signal that cancellation or another scheduler won before execution began."""


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
        docker_run_root: str | Path | None = None,
        kubernetes_runtime_settings: KubernetesRuntimeSettings | None = None,
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
        self.docker_run_root = docker_run_root
        self.kubernetes_runtime_settings = (
            kubernetes_runtime_settings or KubernetesRuntimeSettings()
        )
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
                run_root=self.docker_run_root,
                event_bus=self.event_bus,
            )
        if self.runtime_mode == "kubernetes":
            return KubernetesRuntime(
                workers=self.workers,
                redis_url=self.redis_url,
                event_bus=self.event_bus,
                settings=self.kubernetes_runtime_settings,
            )
        return InProcessRuntime()

    def _build_runtime_for_workers(
        self,
        workers: WorkerImageMap,
    ) -> DockerRuntime | KubernetesRuntime | InProcessRuntime:
        """Build a one-off runtime for dynamic worker mappings."""
        if self.runtime_mode == "docker":
            return DockerRuntime(
                workers=workers,
                redis_url=self.redis_url,
                run_root=self.docker_run_root,
                event_bus=self.event_bus,
            )
        if self.runtime_mode == "kubernetes":
            return KubernetesRuntime(
                workers=workers,
                redis_url=self.redis_url,
                event_bus=self.event_bus,
                settings=self.kubernetes_runtime_settings,
            )
        return self.runtime

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
                    metadata=goblin_job_metadata(definition),
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
                after=job.created_at,
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
            max_project_running = policy.concurrency.max_project_running if policy else None
            if max_project_running is not None:
                active_count = self.store.count_active_project_jobs(
                    job.project_id,
                    exclude_job_id=job.id,
                )
                if active_count >= max_project_running:
                    message = (
                        "deferred by project concurrency policy: "
                        f"active={active_count} max_project_running={max_project_running}"
                    )
                    self.store.finish_job(
                        job.id,
                        status="queued",
                        due_at=current,
                        last_error=message,
                    )
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
                            "scope": "project",
                            "max_project_running": max_project_running,
                            "active_count": active_count,
                            "reason": message,
                        },
                    )
                    continue
            max_running = policy.concurrency.max_running if policy else None
            if max_running is not None:
                active_count = self.store.count_active_jobs(job.kind, exclude_job_id=job.id)
                if active_count >= max_running:
                    message = (
                        "deferred by goblin concurrency policy: "
                        f"active={active_count} max_running={max_running}"
                    )
                    self.store.finish_job(
                        job.id,
                        status="queued",
                        due_at=current,
                        last_error=message,
                    )
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
                            "scope": "goblin",
                            "max_running": max_running,
                            "active_count": active_count,
                            "reason": message,
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
                after=job.created_at,
            )
            runnable.append(job)
        return runnable

    def run_claimed_job(self, job: JobRecord, now: datetime | None = None) -> RunRecord:
        """Execute one leased job and persist both run and final job status."""
        requested_start = _ensure_utc(now or utc_now())
        attempt = job.attempt_count + 1
        if not self.store.try_mark_job_running(
            job.id,
            attempt_count=attempt,
            expected_lease_owner=self.worker_id,
        ):
            raise JobAttemptSuperseded(
                f"job {job.id!r} no longer belongs to scheduler {self.worker_id!r}"
            )
        running_event = self.event_bus.emit(
            "job.running",
            source="scheduler",
            project_id=job.project_id,
            job_id=job.id,
            schedule_id=job.schedule_id,
            fanout_id=job.fanout_id,
            scheduler_id=self.worker_id,
            payload={"kind": job.kind, "attempt": attempt},
            after=job.created_at,
        )
        started_at = causally_after(running_event.created_at, candidate=requested_start)
        started_monotonic = time.monotonic()

        runtime = self.runtime
        runtime_workers = self.workers
        runtime_registry = self.registry
        runtime_input = job.input
        notebook_record = None
        if self.runtime_mode in {"docker", "kubernetes"}:
            try:
                definition = self.registry.get(job.kind)
            except RegistryError:
                notebook_record = self.store.get_notebook_goblin(job.kind)
                if notebook_record is None:
                    raise
                definition = notebook_definition(notebook_record)
                runtime_workers = notebook_worker_map(notebook_record)
                runtime_registry = GoblinRegistry.from_definitions([definition])
                runtime = self._build_runtime_for_workers(runtime_workers)
                runtime_input = notebook_worker_input(notebook_record, job.input)
            entrypoint = None
        else:
            try:
                definition, entrypoint = self.registry.resolve(job.kind)
            except RegistryError:
                notebook_record = self.store.get_notebook_goblin(job.kind)
                if notebook_record is None:
                    raise
                definition = notebook_definition(notebook_record)
                entrypoint = None
                runtime_input = notebook_worker_input(notebook_record, job.input)
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
        if isinstance(runtime, DockerRuntime | KubernetesRuntime):
            try:
                validation_error = self._validate_before_container_run(
                    job,
                    definition.kind,
                    registry=runtime_registry,
                    workers=runtime_workers,
                    runtime=runtime,
                    input_payload=runtime_input,
                    notebook_source_hash=(
                        notebook_record.source_hash if notebook_record is not None else None
                    ),
                    resource_policy=resource_policy.compact() if resource_policy else {},
                )
            except Exception as error:  # validation is a job boundary, not a scheduler boundary
                validation_error = (
                    "Goblin image validation could not start; the job was failed and its lease "
                    "was released. "
                    f"{type(error).__name__}: {error}"
                )
            if validation_error is not None:
                result = GoblinResult.failed(error=validation_error)
                finished_at = self._finish_attempt_timestamp(job.id, started_at)
                run = RunRecord(
                    id=context.run_id,
                    job_id=job.id,
                    kind=definition.kind,
                    project_id=job.project_id,
                    attempt=attempt,
                    status="failed",
                    started_at=started_at,
                    finished_at=finished_at,
                    result=result,
                    error=validation_error,
                    timeout_seconds=job.timeout_seconds,
                    max_retries=job.max_retries,
                    leased_until=job.leased_until,
                    resource_policy=resource_policy.compact() if resource_policy else None,
                )
                finalization = self.store.finalize_job_attempt(
                    run,
                    status="failed",
                    last_error=validation_error,
                    expected_lease_owner=self.worker_id,
                )
                run = finalization.run
                if finalization.outcome == "finalized":
                    self.event_bus.emit(
                        "validation.scheduling_rejected",
                        source="scheduler",
                        project_id=job.project_id,
                        job_id=job.id,
                        run_id=run.id,
                        schedule_id=job.schedule_id,
                        fanout_id=job.fanout_id,
                        scheduler_id=self.worker_id,
                        payload={"kind": job.kind, "error": validation_error},
                        after=run.finished_at,
                    )
                audit(
                    self.store,
                    action="validation.scheduling_rejected",
                    outcome="failure",
                    project_id=job.project_id,
                    resource_type="job",
                    resource_id=job.id,
                    detail={
                        "kind": job.kind,
                        "error": validation_error,
                        "job_transition": finalization.outcome,
                    },
                )
                return run
            result = runtime.run(
                definition,
                entrypoint,
                runtime_input,
                context,
                timeout_seconds=job.timeout_seconds,
                resource_policy=resource_policy,
            )
        elif notebook_record is not None:
            result = GoblinResult.failed(
                error="notebook-defined goblins require docker or kubernetes runtime"
            )
        else:
            result = runtime.run(definition, entrypoint, runtime_input, context)
        finished_at = self._finish_attempt_timestamp(job.id, started_at)
        elapsed_seconds = time.monotonic() - started_monotonic
        status = _status_for_result(result, elapsed_seconds, job.timeout_seconds)
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
        retrying = status == "failed" and attempt <= job.max_retries
        finalization = self.store.finalize_job_attempt(
            run,
            status="retrying" if retrying else status,
            last_error=error,
            due_at=requested_start if retrying else None,
            expected_lease_owner=self.worker_id,
        )
        run = finalization.run

        if finalization.outcome != "finalized":
            return run
        if retrying:
            event_type = "job.retrying"
        else:
            event_type = f"job.{status}"
        self.event_bus.emit(
            event_type,
            source="scheduler",
            project_id=job.project_id,
            job_id=job.id,
            run_id=run.id,
            schedule_id=job.schedule_id,
            fanout_id=job.fanout_id,
            scheduler_id=self.worker_id,
            payload={"kind": job.kind, "attempt": attempt, "error": error},
            after=run.finished_at,
        )
        return run

    def _finish_attempt_timestamp(self, job_id: str, started_at: datetime) -> datetime:
        """Place Run completion after its start and latest persisted worker event."""
        return causally_after(
            started_at,
            self.store.latest_event_created_at(job_id=job_id),
            candidate=utc_now(),
        )

    def _validate_before_container_run(
        self,
        job: JobRecord,
        kind: str,
        *,
        registry: GoblinRegistry | None = None,
        workers: WorkerImageMap | None = None,
        runtime: DockerRuntime | KubernetesRuntime | InProcessRuntime | None = None,
        input_payload: dict | None = None,
        notebook_source_hash: str | None = None,
        resource_policy: dict,
    ) -> str | None:
        """Ensure the current worker image identity has passed contract validation."""
        worker_map = workers or self.workers
        active_registry = registry or self.registry
        active_runtime = runtime or self.runtime
        validation_runtime = (
            "kubernetes" if isinstance(active_runtime, KubernetesRuntime) else "docker"
        )
        payload = job.input if input_payload is None else input_payload
        if worker_map is None:
            return format_validation_gate_error(
                kind=kind,
                image=None,
                reason="worker image map is required for validation",
                runtime=validation_runtime,
            )
        try:
            worker = worker_map.get(kind)
        except WorkerConfigError as error:
            return format_validation_gate_error(
                kind=kind,
                image=None,
                reason=str(error),
                runtime=validation_runtime,
            )
        validation_run_root = (
            active_runtime.run_root if isinstance(active_runtime, DockerRuntime) else None
        )
        docker_executable = (
            active_runtime.docker_executable
            if isinstance(active_runtime, DockerRuntime)
            else "docker"
        )
        validation_policy = dict(resource_policy)
        if isinstance(active_runtime, KubernetesRuntime):
            image_digest, image_error = (
                active_runtime.settings.validation_image_identity(worker.image, kind),
                None,
            )
            validation_policy["kubernetes_workload_security"] = (
                active_runtime.settings.effective_workload_security(kind)
            )
        else:
            image_digest, image_error = inspect_image_identity(docker_executable, worker.image)
        validation_identity = (
            notebook_validation_identity(image_digest, notebook_source_hash)
            if notebook_source_hash is not None
            else image_digest
        )
        latest_for_kind = self.store.latest_worker_validation_for_kind(kind)
        if image_error is not None or image_digest is None:
            error = image_error or f"worker image digest unavailable: {worker.image}"
            result = validate_workers(
                registry=active_registry,
                workers=worker_map,
                input_payload=payload,
                kinds=[kind],
                prebuilt_image=True,
                redis_url=self.redis_url,
                run_root=validation_run_root,
            )[0]
            if notebook_source_hash is not None:
                result = result.model_copy(
                    update={
                        "image_digest": notebook_validation_identity(
                            result.image_digest,
                            notebook_source_hash,
                        )
                    }
                )
            self.store.save_worker_validation(
                validation_record(result, effective_policy=validation_policy)
            )
            return format_validation_gate_error(
                kind=kind,
                image=worker.image,
                image_digest=validation_identity,
                stale_from_digest=latest_for_kind.image_digest if latest_for_kind else None,
                reason=error,
                runtime=validation_runtime,
            )
        cached = self.store.get_latest_worker_validation(
            kind=kind,
            image_digest=validation_identity or image_digest,
            contract_version=GOBLIN_CONTAINER_CONTRACT_VERSION,
            validator_version=VALIDATOR_VERSION,
        )
        if cached is not None and cached.status == "passed":
            return None
        if isinstance(active_runtime, KubernetesRuntime):
            return format_validation_gate_error(
                kind=kind,
                image=worker.image,
                image_digest=validation_identity or image_digest,
                stale_from_digest=latest_for_kind.image_digest if latest_for_kind else None,
                reason="no current Kubernetes validation proof exists; validate first",
                runtime=validation_runtime,
            )
        results = validate_workers(
            registry=active_registry,
            workers=worker_map,
            input_payload=payload,
            kinds=[kind],
            prebuilt_image=True,
            timeout_seconds=job.timeout_seconds,
            redis_url=self.redis_url,
            run_root=validation_run_root,
        )
        result = results[0]
        if notebook_source_hash is not None:
            result = result.model_copy(
                update={
                    "image_digest": notebook_validation_identity(
                        result.image_digest,
                        notebook_source_hash,
                    )
                }
            )
        self.store.save_worker_validation(
            validation_record(result, effective_policy=validation_policy)
        )
        if not result.ok:
            stale_from_digest = (
                latest_for_kind.image_digest
                if latest_for_kind
                and latest_for_kind.status == "passed"
                and latest_for_kind.image_digest != image_digest
                else None
            )
            return format_validation_gate_error(
                kind=kind,
                image=result.image or worker.image,
                image_digest=result.image_digest or validation_identity or image_digest,
                stale_from_digest=stale_from_digest,
                contract_version=result.contract_version,
                validator_version=result.validator_version,
                reason=result.error or "worker validation failed",
                runtime=validation_runtime,
            )
        return None

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
            try:
                runs.append(self.run_claimed_job(job, current))
            except JobAttemptSuperseded:
                continue
            except Exception as error:  # one malformed attempt must not end the scheduler loop
                runs.append(
                    record_unexpected_job_failure(
                        store=self.store,
                        event_bus=self.event_bus,
                        scheduler_id=self.worker_id,
                        claimed_job=job,
                        started_at=current,
                        error=error,
                    )
                )
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
    elapsed_seconds: float,
    timeout_seconds: int | None,
) -> str:
    """Map a result envelope plus monotonic elapsed time into a run/job status."""
    if timeout_seconds is not None and elapsed_seconds > timeout_seconds:
        return "timed_out"
    return "completed" if result.status == "success" else "failed"


def _ensure_utc(value: datetime) -> datetime:
    """Normalize scheduler timestamps to timezone-aware UTC values."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def sleep_forever() -> None:  # pragma: no cover - manual helper
    """Keep a foreground process alive for debugger-driven scheduler experiments."""
    while True:
        time.sleep(3600)
