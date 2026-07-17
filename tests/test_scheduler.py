"""Local scheduler tests for schedule materialization, leasing, retries, and timeouts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event, Thread

from goblin_king.contracts import (
    GoblinDefinition,
    GoblinResult,
    JobRecord,
    RunRecord,
    ScheduleRecord,
    WorkerValidationRecord,
    utc_now,
)
from goblin_king.kubernetes_runtime_settings import KubernetesRuntimeSettings
from goblin_king.registry import GoblinRegistry
from goblin_king.resource_policies import ResourcePolicySet
from goblin_king.runtime import DockerRuntime
from goblin_king.scheduler import Scheduler
from goblin_king.store import SQLiteStore
from goblin_king.validation import WorkerValidationResult, kubernetes_image_identity
from goblin_king.workers import WorkerImageDefinition, WorkerImageMap


def build_scheduler(tmp_path: Path) -> tuple[Scheduler, SQLiteStore]:
    """Create a scheduler with a fresh SQLite store and the example registry."""
    store = SQLiteStore(tmp_path / "goblin.sqlite3")
    scheduler = Scheduler(
        registry=GoblinRegistry.from_path("examples/goblins.json"),
        store=store,
        worker_id="test-worker",
        runtime_mode="in-process",
    )
    return scheduler, store


def build_docker_scheduler(tmp_path: Path) -> tuple[Scheduler, SQLiteStore]:
    """Create a Docker-mode scheduler with a fake worker image map for gate tests."""
    store = SQLiteStore(tmp_path / "goblin.sqlite3")
    registry = GoblinRegistry.from_definitions(
        [
            GoblinDefinition(
                kind="example.validation",
                display_name="Example Validation",
                module="container.only",
            )
        ]
    )
    workers = WorkerImageMap.from_definitions(
        {
            "example.validation": WorkerImageDefinition(
                context=tmp_path,
                image="example-validation:local",
            )
        },
        root=tmp_path,
    )
    scheduler = Scheduler(
        registry=registry,
        store=store,
        worker_id="test-worker",
        runtime_mode="docker",
        workers=workers,
    )
    return scheduler, store


def build_kubernetes_scheduler(
    tmp_path: Path,
    *,
    settings: KubernetesRuntimeSettings | None = None,
) -> tuple[Scheduler, SQLiteStore]:
    """Create a Kubernetes-mode scheduler for validation identity gate tests."""
    store = SQLiteStore(tmp_path / "goblin.sqlite3")
    registry = GoblinRegistry.from_definitions(
        [
            GoblinDefinition(
                kind="example.validation",
                display_name="Example Validation",
                module="container.only",
            )
        ]
    )
    workers = WorkerImageMap.from_definitions(
        {
            "example.validation": WorkerImageDefinition(
                context=tmp_path,
                image="registry.example/validation@sha256:abc",
            )
        },
        root=tmp_path,
    )
    scheduler = Scheduler(
        registry=registry,
        store=store,
        worker_id="test-worker",
        runtime_mode="kubernetes",
        workers=workers,
        kubernetes_runtime_settings=settings,
    )
    return scheduler, store


def test_run_once_materializes_due_schedule_and_executes_echo(tmp_path: Path) -> None:
    """Verify one scheduler pass creates and completes a due scheduled job."""
    now = datetime(2026, 6, 9, 12, 0, tzinfo=UTC)
    scheduler, store = build_scheduler(tmp_path)
    store.save_schedule(
        ScheduleRecord(
            id="schedule-1",
            kind="example.echo",
            input={"message": "hello"},
            cron="* * * * *",
            created_at=now,
            next_run_at=now,
        )
    )

    runs = scheduler.run_once(now)
    jobs = store.list_jobs()

    assert len(runs) == 1
    assert runs[0].status == "completed"
    assert jobs[0].status == "completed"
    assert jobs[0].schedule_id == "schedule-1"
    event_types = [event.event_type for event in store.list_events()]
    assert "schedule.materialized" in event_types
    assert "job.leased" in event_types
    assert "job.running" in event_types
    assert "job.completed" in event_types
    assert store.get_heartbeat("test-worker") is not None


def test_immediate_attempts_remain_causal_when_wall_clock_rolls_back(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Order fast success/failure Runs and events independently of wall-clock rollback."""
    now = utc_now()
    scheduler, store = build_scheduler(tmp_path)
    monkeypatch.setattr(scheduler.event_bus, "_publish", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(scheduler.event_bus, "_append_stream", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("goblin_king.events.utc_now", lambda: now - timedelta(seconds=5))
    monkeypatch.setattr("goblin_king.scheduler.utc_now", lambda: now - timedelta(seconds=5))
    for job_id, should_fail in (("job-success", False), ("job-failure", True)):
        store.save_job(
            JobRecord(
                id=job_id,
                kind="example.echo",
                input={"should_fail": should_fail},
                created_at=now,
                due_at=now,
            )
        )

    def immediate_attempt(_definition, _entrypoint, input_payload, context, **_kwargs):
        job_id = context.metadata["job_id"]
        scheduler.event_bus.emit(
            "worker.started",
            source="runtime",
            job_id=job_id,
            run_id=context.run_id,
        )
        failed = bool(input_payload["should_fail"])
        scheduler.event_bus.emit(
            "worker.failed" if failed else "worker.completed",
            source="runtime",
            job_id=job_id,
            run_id=context.run_id,
        )
        return GoblinResult.failed(error="expected") if failed else GoblinResult.ok()

    monkeypatch.setattr(scheduler.runtime, "run", immediate_attempt)

    runs = {run.job_id: run for run in scheduler.run_once(now)}

    assert runs["job-success"].status == "completed"
    assert runs["job-failure"].status == "failed"
    for job_id, run in runs.items():
        events = store.list_events(job_id=job_id)
        assert [event.sequence for event in events] == sorted(
            event.sequence for event in events
        )
        assert all(
            left.created_at < right.created_at
            for left, right in zip(events, events[1:], strict=False)
        )
        assert run.finished_at is not None
        assert run.started_at <= run.finished_at
        running = next(event for event in events if event.event_type == "job.running")
        worker_started = next(event for event in events if event.event_type == "worker.started")
        terminal = events[-1]
        assert running.created_at < run.started_at <= worker_started.created_at
        assert run.finished_at < terminal.created_at


def test_active_cancellation_cannot_be_overwritten_by_worker_completion(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Keep the public cancelled job state when an already-running worker returns later."""
    now = utc_now()
    scheduler, store = build_scheduler(tmp_path)
    store.save_job(
        JobRecord(
            id="job-cancel-race",
            kind="example.echo",
            input={},
            created_at=now,
            due_at=now,
        )
    )
    claimed = scheduler.claim_due_jobs(now)[0]
    entered = Event()
    release = Event()

    def blocked_attempt(*_args, **_kwargs) -> GoblinResult:
        entered.set()
        assert release.wait(5)
        return GoblinResult.ok()

    monkeypatch.setattr(scheduler.runtime, "run", blocked_attempt)
    observed: dict[str, object] = {}

    def execute() -> None:
        observed["run"] = scheduler.run_claimed_job(claimed, now)

    thread = Thread(target=execute)
    thread.start()
    assert entered.wait(5)
    running_runs = store.list_job_runs(claimed.id)
    assert len(running_runs) == 1
    assert running_runs[0].status == "running"
    running_id = running_runs[0].id
    cancelled, changed = store.try_cancel_job(claimed.id)
    assert cancelled is not None
    assert changed is True
    cancelled_event = scheduler.event_bus.emit(
        "job.cancelled",
        source="api",
        job_id=claimed.id,
        after=cancelled.created_at,
    )

    release.set()
    thread.join(5)
    assert not thread.is_alive()

    run = observed["run"]
    assert isinstance(run, RunRecord)
    persisted_job = store.get_job(claimed.id)
    assert persisted_job is not None
    assert persisted_job.status == "cancelled"
    assert persisted_job.lease_owner is None
    assert run.status == "completed"
    assert run.id == running_id
    assert run.finished_at is not None
    assert run.started_at <= run.finished_at
    assert store.get_run(run.id) == run
    events = store.list_events(job_id=claimed.id)
    assert cancelled_event in events
    assert "job.completed" not in {event.event_type for event in events}
    assert [event.sequence for event in events] == sorted(event.sequence for event in events)


def test_validation_gate_reuses_passing_proof(tmp_path: Path, monkeypatch) -> None:
    """Verify cached proof for the current digest avoids re-running validation."""
    scheduler, store = build_docker_scheduler(tmp_path)
    store.save_worker_validation(
        WorkerValidationRecord(
            id="validation-current",
            kind="example.validation",
            image="example-validation:local",
            image_digest="sha256:current",
            contract_version="goblin-king/v1alpha1",
            validator_version="goblin-king-validator/v1",
            validated_at=utc_now(),
            status="passed",
        )
    )
    monkeypatch.setattr(
        "goblin_king.scheduler.inspect_image_identity",
        lambda _docker, _image: ("sha256:current", None),
    )

    def fail_if_called(**_kwargs):
        raise AssertionError("validation should not rerun for current passing proof")

    monkeypatch.setattr("goblin_king.scheduler.validate_workers", fail_if_called)

    error = scheduler._validate_before_container_run(
        JobRecord(
            id="job-1",
            kind="example.validation",
            input={},
            created_at=utc_now(),
        ),
        "example.validation",
        resource_policy={},
    )

    assert error is None


def test_scheduler_separates_declared_validation_input_from_runtime_input(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Validate with reviewed bounded data, then execute the exact queued payload."""
    scheduler, store = build_docker_scheduler(tmp_path)
    scheduler.registry = GoblinRegistry.from_definitions(
        [
            GoblinDefinition(
                kind="example.validation",
                display_name="Example Validation",
                module="container.only",
                metadata={"validation_input": {"ticks": 1}},
            )
        ]
    )
    now = datetime(2026, 7, 17, 2, 0, tzinfo=UTC)
    store.save_job(
        JobRecord(
            id="job-bounded-validation",
            kind="example.validation",
            input={"ticks": 100},
            created_at=now,
            due_at=now,
        )
    )
    claimed = scheduler.claim_due_jobs(now)[0]
    observed: dict[str, object] = {}

    def validate(_job, _kind, **kwargs):
        observed["validation"] = kwargs["input_payload"]
        return None

    def run(_definition, _entrypoint, input_payload, _context, **_kwargs):
        observed["runtime"] = input_payload
        return GoblinResult.ok(data={"completed": True})

    monkeypatch.setattr(scheduler, "_validate_before_container_run", validate)
    monkeypatch.setattr(scheduler.runtime, "run", run)

    result = scheduler.run_claimed_job(claimed, now)

    assert result.status == "completed"
    assert observed == {
        "validation": {"ticks": 1},
        "runtime": {"ticks": 100},
    }


def test_kubernetes_validation_gate_accepts_public_operation_identity(tmp_path: Path) -> None:
    """Verify preflight proof uses the exact identity recorded by Kubernetes validation."""
    scheduler, store = build_kubernetes_scheduler(tmp_path)
    identity = kubernetes_image_identity("registry.example/validation@sha256:abc")
    store.save_worker_validation(
        WorkerValidationRecord(
            id="kubernetes-validation-current",
            kind="example.validation",
            image="registry.example/validation@sha256:abc",
            image_digest=identity,
            contract_version="goblin-king/v1alpha1",
            validator_version="goblin-king-validator/v1",
            validated_at=utc_now(),
            status="passed",
        )
    )

    error = scheduler._validate_before_container_run(
        JobRecord(
            id="job-kubernetes",
            kind="example.validation",
            input={},
            created_at=utc_now(),
        ),
        "example.validation",
        resource_policy={},
    )

    assert error is None


def test_restricted_gate_rejects_legacy_proof_and_uses_per_kind_identity(
    tmp_path: Path,
) -> None:
    """Verify legacy proof cannot authorize a restricted per-kind workload contract."""
    settings = KubernetesRuntimeSettings.model_validate(
        {
            "workload_security_profile": "restricted-v1",
            "restricted_workload": {
                "worker_service_account_names": {
                    "example.validation": "goblin-validation-reader"
                }
            },
        }
    )
    scheduler, store = build_kubernetes_scheduler(tmp_path, settings=settings)
    image = "registry.example/validation@sha256:abc"
    legacy_identity = kubernetes_image_identity(image)
    restricted_identity = settings.validation_image_identity(
        image,
        "example.validation",
    )
    store.save_worker_validation(
        WorkerValidationRecord(
            id="legacy-kubernetes-validation",
            kind="example.validation",
            image=image,
            image_digest=legacy_identity,
            contract_version="goblin-king/v1alpha1",
            validator_version="goblin-king-validator/v1",
            validated_at=utc_now(),
            status="passed",
        )
    )
    job = JobRecord(
        id="job-kubernetes-restricted",
        kind="example.validation",
        input={},
        created_at=utc_now(),
    )

    assert scheduler._validate_before_container_run(
        job,
        "example.validation",
        resource_policy={},
    ) is not None

    store.save_worker_validation(
        WorkerValidationRecord(
            id="restricted-kubernetes-validation",
            kind="example.validation",
            image=image,
            image_digest=restricted_identity,
            contract_version="goblin-king/v1alpha1",
            validator_version="goblin-king-validator/v1",
            validated_at=utc_now(),
            status="passed",
            effective_policy={
                "kubernetes_workload_security": (
                    settings.effective_workload_security("example.validation")
                )
            },
        )
    )

    assert scheduler._validate_before_container_run(
        job,
        "example.validation",
        resource_policy={},
    ) is None
    assert restricted_identity != legacy_identity


def test_kubernetes_validation_gate_names_attainable_repair_command(tmp_path: Path) -> None:
    """Verify missing proof points operators to the Kubernetes validation path."""
    scheduler, _ = build_kubernetes_scheduler(tmp_path)

    error = scheduler._validate_before_container_run(
        JobRecord(
            id="job-kubernetes",
            kind="example.validation",
            input={},
            created_at=utc_now(),
        ),
        "example.validation",
        resource_policy={},
    )

    assert error is not None
    assert "--runtime kubernetes" in error
    assert "--build" not in error


def test_validation_gate_blocks_failed_jit_validation(tmp_path: Path, monkeypatch) -> None:
    """Verify missing proof cannot execute when just-in-time validation fails."""
    scheduler, _ = build_docker_scheduler(tmp_path)
    monkeypatch.setattr(
        "goblin_king.scheduler.inspect_image_identity",
        lambda _docker, _image: ("sha256:current", None),
    )
    monkeypatch.setattr(
        "goblin_king.scheduler.validate_workers",
        lambda **_kwargs: [
            WorkerValidationResult(
                kind="example.validation",
                ok=False,
                image="example-validation:local",
                image_digest="sha256:current",
                error="worker did not write result.json",
            )
        ],
    )

    error = scheduler._validate_before_container_run(
        JobRecord(
            id="job-1",
            kind="example.validation",
            input={},
            created_at=utc_now(),
        ),
        "example.validation",
        resource_policy={},
    )

    assert error is not None
    assert "Goblin image failed contract validation" in error
    assert "Validate first, then schedule." in error
    assert "worker did not write result.json" in error
    assert "goblin-king workers validate --kind example.validation" in error


def test_validation_exception_fails_jobs_without_ending_the_scheduler(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Turn validation setup exceptions into terminal Runs and continue the pass."""
    now = datetime(2026, 7, 12, 12, 0, tzinfo=UTC)
    scheduler, store = build_docker_scheduler(tmp_path)
    monkeypatch.setattr(scheduler.event_bus, "_publish", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(scheduler.event_bus, "_append_stream", lambda *_args, **_kwargs: None)
    for job_id in ("job-first", "job-second"):
        store.save_job(
            JobRecord(
                id=job_id,
                kind="example.validation",
                input={},
                created_at=now,
                due_at=now,
            )
        )

    def fail_validation(*_args, **_kwargs):
        raise OSError(30, "Read-only file system")

    monkeypatch.setattr(scheduler, "_validate_before_container_run", fail_validation)

    runs = scheduler.run_once(now)
    jobs = {job.id: job for job in store.list_jobs()}

    assert {run.job_id for run in runs} == {"job-first", "job-second"}
    assert all(run.status == "failed" for run in runs)
    assert all("lease was released" in (run.error or "") for run in runs)
    assert all(job.status == "failed" for job in jobs.values())
    assert all(job.lease_owner is None and job.leased_until is None for job in jobs.values())
    assert len(store.list_events(event_type="validation.scheduling_rejected")) == 2


def test_scheduler_passes_configured_root_to_docker_runtimes(tmp_path: Path) -> None:
    """Use one writable root for active, dynamic, and validation Docker runtimes."""
    scheduler, _ = build_docker_scheduler(tmp_path)
    configured = tmp_path / "writable-data" / "runs"
    scheduler.docker_run_root = configured
    scheduler.runtime = scheduler._build_runtime()

    assert isinstance(scheduler.runtime, DockerRuntime)
    assert scheduler.runtime.run_root == configured


def test_replacement_scheduler_executes_an_expired_running_job(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Recover a job stranded after its prior scheduler marked it running."""
    now = utc_now()
    scheduler, store = build_scheduler(tmp_path)
    monkeypatch.setattr(scheduler.event_bus, "_publish", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(scheduler.event_bus, "_append_stream", lambda *_args, **_kwargs: None)
    store.save_job(
        JobRecord(
            id="job-stranded",
            kind="example.echo",
            input={"message": "recovered"},
            created_at=now - timedelta(minutes=2),
            due_at=now - timedelta(minutes=2),
            status="running",
            attempt_count=1,
            lease_owner="stopped-scheduler",
            leased_until=now - timedelta(seconds=1),
        )
    )

    runs = scheduler.run_once(now)
    recovered = store.get_job("job-stranded")

    assert len(runs) == 1
    assert runs[0].status == "completed"
    assert runs[0].attempt == 2
    assert recovered is not None
    assert recovered.status == "completed"
    assert recovered.attempt_count == 2
    assert recovered.lease_owner is None
    assert recovered.leased_until is None


def test_two_scheduler_loops_cannot_reclaim_an_active_synchronous_batch(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Renew running and waiting leases while one scheduler executes its batch serially."""
    now = utc_now()
    db_path = tmp_path / "goblin.sqlite3"
    registry = GoblinRegistry.from_path("examples/goblins.json")
    first_store = SQLiteStore(db_path)
    second_store = SQLiteStore(db_path)
    first_scheduler = Scheduler(
        registry=registry,
        store=first_store,
        worker_id="scheduler-a",
        lease_seconds=1,
        claim_limit=2,
        runtime_mode="in-process",
    )
    second_scheduler = Scheduler(
        registry=registry,
        store=second_store,
        worker_id="scheduler-b",
        lease_seconds=1,
        claim_limit=2,
        runtime_mode="in-process",
    )
    for scheduler in (first_scheduler, second_scheduler):
        monkeypatch.setattr(scheduler.event_bus, "_publish", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(scheduler.event_bus, "_append_stream", lambda *_args, **_kwargs: None)

    for index, job_id in enumerate(("job-running", "job-waiting")):
        first_store.save_job(
            JobRecord(
                id=job_id,
                kind="example.echo",
                input={"message": job_id},
                created_at=now + timedelta(microseconds=index),
                due_at=now,
            )
        )

    entered = Event()
    release = Event()
    attempts: list[str] = []

    def serial_runtime(_definition, _entrypoint, _input, context, **_kwargs) -> GoblinResult:
        job_id = context.metadata["job_id"]
        attempts.append(job_id)
        if job_id == "job-running":
            entered.set()
            assert release.wait(5)
        return GoblinResult.ok()

    def duplicate_runtime(*_args, **_kwargs) -> GoblinResult:
        raise AssertionError("a second scheduler reclaimed an actively maintained lease")

    monkeypatch.setattr(first_scheduler.runtime, "run", serial_runtime)
    monkeypatch.setattr(second_scheduler.runtime, "run", duplicate_runtime)
    observed: dict[str, list[RunRecord]] = {}

    def execute_batch() -> None:
        observed["runs"] = first_scheduler.run_once()

    thread = Thread(target=execute_batch)
    thread.start()
    assert entered.wait(5)

    # Cross the original one-second deadline. Both the executing job and the second
    # synchronously waiting job must still belong to the first scheduler.
    assert Event().wait(1.25) is False
    assert second_scheduler.run_once() == []

    active_jobs = {job.id: job for job in second_store.list_jobs()}
    assert active_jobs["job-running"].lease_owner == "scheduler-a"
    assert active_jobs["job-running"].leased_until is not None
    assert active_jobs["job-running"].leased_until > utc_now()
    assert active_jobs["job-waiting"].lease_owner == "scheduler-a"
    assert active_jobs["job-waiting"].leased_until is not None
    assert active_jobs["job-waiting"].leased_until > utc_now()

    release.set()
    thread.join(5)
    assert not thread.is_alive()
    assert attempts == ["job-running", "job-waiting"]
    assert len(observed["runs"]) == 2
    completed = {job.id: job for job in second_store.list_jobs()}
    assert all(job.status == "completed" for job in completed.values())
    assert all(job.attempt_count == 1 for job in completed.values())


def test_unexpected_runtime_exception_fails_attempt_and_continues_scheduler(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Keep unrelated claimed work moving after an execution adapter raises."""
    now = utc_now()
    scheduler, store = build_scheduler(tmp_path)
    monkeypatch.setattr(scheduler.event_bus, "_publish", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(scheduler.event_bus, "_append_stream", lambda *_args, **_kwargs: None)
    for job_id in ("job-first", "job-second"):
        store.save_job(
            JobRecord(
                id=job_id,
                kind="example.echo",
                input={"message": job_id},
                created_at=now,
                due_at=now,
            )
        )

    running_ids: dict[str, str] = {}

    def fail_runtime(_definition, _entrypoint, _input, context, **_kwargs):
        running = store.get_run(context.run_id)
        assert running is not None
        assert running.status == "running"
        running_ids[running.job_id] = running.id
        raise RuntimeError("adapter exploded")

    monkeypatch.setattr(scheduler.runtime, "run", fail_runtime)

    runs = scheduler.run_once(now)
    jobs = store.list_jobs()

    assert {run.job_id for run in runs} == {"job-first", "job-second"}
    assert all(run.status == "failed" for run in runs)
    assert {run.job_id: run.id for run in runs} == running_ids
    assert len(store.list_job_runs("job-first")) == 1
    assert len(store.list_job_runs("job-second")) == 1
    assert all("adapter exploded" in (run.error or "") for run in runs)
    assert all(job.status == "failed" for job in jobs)
    assert all(job.lease_owner is None and job.leased_until is None for job in jobs)
    failure_events = store.list_events(event_type="job.failed")
    assert len(failure_events) == 2
    assert all(event.payload["scheduler_exception"] is True for event in failure_events)


def test_cancellation_during_retry_exception_preserves_job_and_attempt_evidence(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Keep cancellation terminal while retaining a later retry attempt that raises."""
    now = utc_now()
    scheduler, store = build_scheduler(tmp_path)
    monkeypatch.setattr(scheduler.event_bus, "_publish", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(scheduler.event_bus, "_append_stream", lambda *_args, **_kwargs: None)
    store.save_job(
        JobRecord(
            id="job-cancelled-retry",
            kind="example.echo",
            input={},
            created_at=now,
            due_at=now,
            max_retries=1,
        )
    )
    entered = Event()
    release = Event()
    attempts = {"count": 0}

    def runtime(*_args, **_kwargs) -> GoblinResult:
        attempts["count"] += 1
        if attempts["count"] == 1:
            return GoblinResult.failed(error="retry me")
        entered.set()
        assert release.wait(5)
        raise RuntimeError("cancelled adapter stopped")

    monkeypatch.setattr(scheduler.runtime, "run", runtime)
    first = scheduler.run_once(now)
    observed: dict[str, object] = {}

    def execute_retry() -> None:
        observed["runs"] = scheduler.run_once(now)

    thread = Thread(target=execute_retry)
    thread.start()
    assert entered.wait(5)
    live_attempts = store.list_job_runs("job-cancelled-retry")
    assert [run.attempt for run in live_attempts] == [1, 2]
    assert live_attempts[-1].status == "running"
    retry_running_id = live_attempts[-1].id
    cancelled, changed = store.try_cancel_job("job-cancelled-retry")
    assert cancelled is not None
    assert changed is True
    scheduler.event_bus.emit(
        "job.cancelled",
        source="api",
        job_id=cancelled.id,
        after=cancelled.created_at,
    )
    release.set()
    thread.join(5)
    assert not thread.is_alive()

    retry_runs = observed["runs"]
    assert isinstance(retry_runs, list)
    assert len(first) == 1
    assert len(retry_runs) == 1
    assert [run.attempt for run in store.list_job_runs(cancelled.id)] == [1, 2]
    assert retry_runs[0].status == "failed"
    assert retry_runs[0].id == retry_running_id
    final_job = store.get_job(cancelled.id)
    assert final_job is not None
    assert final_job.status == "cancelled"
    assert len(store.list_events(event_type="job.failed", job_id=cancelled.id)) == 0


def test_validation_gate_reports_stale_digest_when_revalidation_fails(
    tmp_path: Path, monkeypatch
) -> None:
    """Verify old digest proof is named when a changed image fails revalidation."""
    scheduler, store = build_docker_scheduler(tmp_path)
    store.save_worker_validation(
        WorkerValidationRecord(
            id="validation-old",
            kind="example.validation",
            image="example-validation:local",
            image_digest="sha256:old",
            contract_version="goblin-king/v1alpha1",
            validator_version="goblin-king-validator/v1",
            validated_at=utc_now(),
            status="passed",
        )
    )
    monkeypatch.setattr(
        "goblin_king.scheduler.inspect_image_identity",
        lambda _docker, _image: ("sha256:new", None),
    )
    monkeypatch.setattr(
        "goblin_king.scheduler.validate_workers",
        lambda **_kwargs: [
            WorkerValidationResult(
                kind="example.validation",
                ok=False,
                image="example-validation:local",
                image_digest="sha256:new",
                error="result envelope invalid: bad json",
            )
        ],
    )

    error = scheduler._validate_before_container_run(
        JobRecord(
            id="job-1",
            kind="example.validation",
            input={},
            created_at=utc_now(),
        ),
        "example.validation",
        resource_policy={},
    )

    assert error is not None
    assert "Image digest: sha256:new" in error
    assert "Previous validation digest: sha256:old" in error
    assert "result envelope invalid" in error


def test_disabled_and_future_schedules_do_not_materialize(tmp_path: Path) -> None:
    """Verify scheduler respects disabled schedules and future next-run timestamps."""
    now = datetime(2026, 6, 9, 12, 0, tzinfo=UTC)
    scheduler, store = build_scheduler(tmp_path)
    store.save_schedule(
        ScheduleRecord(
            id="disabled",
            kind="example.echo",
            input={},
            cron="* * * * *",
            enabled=False,
            created_at=now,
            next_run_at=now,
        )
    )
    store.save_schedule(
        ScheduleRecord(
            id="future",
            kind="example.echo",
            input={},
            cron="* * * * *",
            created_at=now,
            next_run_at=now + timedelta(minutes=1),
        )
    )

    assert scheduler.run_once(now) == []
    assert store.list_jobs() == []


def test_failing_goblin_retries_then_fails(tmp_path: Path) -> None:
    """Verify failed jobs retry until max_retries is exhausted."""
    now = datetime(2026, 6, 9, 12, 0, tzinfo=UTC)
    store = SQLiteStore(tmp_path / "goblin.sqlite3")
    scheduler = Scheduler(
        registry=GoblinRegistry.from_path("tests/fixtures/failing-registry.json"),
        store=store,
        worker_id="test-worker",
        runtime_mode="in-process",
    )
    store.save_schedule(
        ScheduleRecord(
            id="failing",
            kind="example.fail",
            input={},
            cron="* * * * *",
            created_at=now,
            next_run_at=now,
            max_retries=1,
        )
    )

    first = scheduler.run_once(now)
    first_job = store.list_jobs()[0]
    second = scheduler.run_once(now + timedelta(seconds=1))
    second_job = store.list_jobs()[0]

    assert first[0].status == "failed"
    assert first_job.status == "retrying"
    assert second[0].status == "failed"
    assert second_job.status == "failed"
    assert second_job.attempt_count == 2
    assert all(
        run.finished_at is not None and run.started_at <= run.finished_at
        for run in first + second
    )
    events = store.list_events(job_id=second_job.id)
    assert [event.sequence for event in events] == sorted(event.sequence for event in events)


def test_timeout_configuration_marks_overdue_run(tmp_path: Path) -> None:
    """Verify timeout metadata can mark a completed in-process call as timed out."""
    now = datetime(2026, 6, 9, 12, 0, tzinfo=UTC)
    scheduler, store = build_scheduler(tmp_path)
    store.save_schedule(
        ScheduleRecord(
            id="timeout",
            kind="example.echo",
            input={},
            cron="* * * * *",
            created_at=now,
            next_run_at=now,
            timeout_seconds=0,
        )
    )

    runs = scheduler.run_once(now)
    job = store.list_jobs()[0]

    assert runs[0].status == "timed_out"
    assert runs[0].finished_at is not None
    assert runs[0].started_at <= runs[0].finished_at
    assert job.status == "timed_out"


def test_scheduler_reload_discovery_swaps_active_registry(tmp_path: Path) -> None:
    """Verify a live scheduler can use refreshed goblin definitions."""
    scheduler, _ = build_scheduler(tmp_path)
    refreshed = GoblinRegistry.from_definitions(
        [
            GoblinDefinition(
                kind="project.reloaded",
                display_name="Project Reloaded",
                module="examples.goblins.echo",
            )
        ]
    )

    version = scheduler.reload_discovery(registry=refreshed)

    assert version == 2
    assert scheduler.discovery_version == 2
    assert scheduler.registry.get("project.reloaded").display_name == "Project Reloaded"


def test_scheduler_materializes_project_definition_metadata(tmp_path: Path) -> None:
    """Verify project-defined scheduled jobs preserve source definition metadata."""
    now = datetime(2026, 6, 9, 12, 0, tzinfo=UTC)
    store = SQLiteStore(tmp_path / "goblin.sqlite3")
    registry = GoblinRegistry.from_definitions(
        [
            GoblinDefinition(
                kind="project.scheduled",
                display_name="Project Scheduled",
                module="examples.goblins.echo",
                metadata={"source": "project-config", "labels": {"demo": "true"}},
            )
        ]
    )
    scheduler = Scheduler(
        registry=registry,
        store=store,
        worker_id="test-worker",
        runtime_mode="in-process",
    )
    store.save_schedule(
        ScheduleRecord(
            id="project-schedule",
            kind="project.scheduled",
            input={"message": "hello"},
            cron="* * * * *",
            created_at=now,
            next_run_at=now,
        )
    )

    runs = scheduler.run_once(now)
    job = store.list_jobs()[0]

    assert runs[0].status == "completed"
    assert job.metadata["goblin_source"] == "project-config"
    assert job.metadata["goblin_definition"]["kind"] == "project.scheduled"
    assert job.metadata["goblin_definition"]["metadata"]["labels"]["demo"] == "true"


def test_scheduler_persists_effective_resource_policy(tmp_path: Path) -> None:
    """Verify scheduled jobs and runs preserve the resolved resource policy."""
    now = datetime(2026, 6, 9, 12, 0, tzinfo=UTC)
    store = SQLiteStore(tmp_path / "goblin.sqlite3")
    scheduler = Scheduler(
        registry=GoblinRegistry.from_path("examples/goblins.json"),
        store=store,
        worker_id="test-worker",
        runtime_mode="in-process",
        resource_policies=ResourcePolicySet.model_validate(
            {
                "defaults": {
                    "timeout_seconds": 45,
                    "memory": {"limit": "256Mi"},
                },
                "ceilings": {
                    "timeout_seconds": 60,
                    "max_retries": 2,
                    "memory": {"limit": "512Mi"},
                },
            }
        ),
    )
    store.save_schedule(
        ScheduleRecord(
            id="policy-schedule",
            kind="example.echo",
            input={"message": "hello"},
            cron="* * * * *",
            created_at=now,
            next_run_at=now,
        )
    )

    runs = scheduler.run_once(now)
    job = store.list_jobs()[0]

    assert job.metadata["resource_policy"]["timeout_seconds"] == 45
    assert job.timeout_seconds == 45
    assert job.max_retries == 0
    assert runs[0].resource_policy == job.metadata["resource_policy"]


def test_scheduler_defers_claim_when_concurrency_policy_is_full(tmp_path: Path) -> None:
    """Verify concurrency caps release extra claims before worker execution."""
    now = datetime(2026, 6, 9, 12, 0, tzinfo=UTC)
    store = SQLiteStore(tmp_path / "goblin.sqlite3")
    scheduler = Scheduler(
        registry=GoblinRegistry.from_path("examples/goblins.json"),
        store=store,
        worker_id="test-worker",
        runtime_mode="in-process",
        resource_policies=ResourcePolicySet.model_validate(
            {
                "defaults": {"concurrency": {"max_running": 1}},
                "ceilings": {"concurrency": {"max_running": 2}},
            }
        ),
    )
    store.save_job(
        JobRecord(
            id="already-running",
            kind="example.echo",
            input={},
            created_at=now,
            status="running",
        )
    )
    store.save_job(
        JobRecord(
            id="queued",
            kind="example.echo",
            input={},
            created_at=now,
            status="queued",
            metadata={"resource_policy": {"concurrency": {"max_running": 1}}},
        )
    )

    claimed = scheduler.claim_due_jobs(now)

    queued = store.get_job("queued")
    assert claimed == []
    assert queued is not None
    assert queued.status == "queued"
    assert queued.last_error == (
        "deferred by goblin concurrency policy: active=1 max_running=1"
    )
    assert "resource_policy.concurrency_deferred" in [
        event.event_type for event in store.list_events()
    ]
    assert store.list_events()[0].payload["scope"] == "goblin"


def test_scheduler_defers_claim_when_project_concurrency_policy_is_full(
    tmp_path: Path,
) -> None:
    """Verify project-wide concurrency caps defer extra jobs in the same project."""
    now = datetime(2026, 6, 9, 12, 0, tzinfo=UTC)
    store = SQLiteStore(tmp_path / "goblin.sqlite3")
    scheduler = Scheduler(
        registry=GoblinRegistry.from_path("examples/goblins.json"),
        store=store,
        worker_id="test-worker",
        runtime_mode="in-process",
    )
    store.save_job(
        JobRecord(
            id="already-running",
            kind="example.environment",
            project_id="project-a",
            input={},
            created_at=now,
            status="running",
        )
    )
    store.save_job(
        JobRecord(
            id="other-project",
            kind="example.echo",
            project_id="project-b",
            input={},
            created_at=now,
            status="running",
        )
    )
    store.save_job(
        JobRecord(
            id="queued",
            kind="example.echo",
            project_id="project-a",
            input={},
            created_at=now,
            status="queued",
            metadata={
                "resource_policy": {
                    "concurrency": {"max_project_running": 1},
                }
            },
        )
    )

    claimed = scheduler.claim_due_jobs(now)

    queued = store.get_job("queued")
    events = store.list_events()
    assert claimed == []
    assert queued is not None
    assert queued.status == "queued"
    assert queued.last_error == (
        "deferred by project concurrency policy: active=1 max_project_running=1"
    )
    assert events[0].event_type == "resource_policy.concurrency_deferred"
    assert events[0].payload["scope"] == "project"
    assert events[0].payload["max_project_running"] == 1
