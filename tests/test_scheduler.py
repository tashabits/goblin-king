"""Local scheduler tests for schedule materialization, leasing, retries, and timeouts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from goblin_king.contracts import (
    GoblinDefinition,
    JobRecord,
    ScheduleRecord,
    WorkerValidationRecord,
    utc_now,
)
from goblin_king.registry import GoblinRegistry
from goblin_king.resource_policies import ResourcePolicySet
from goblin_king.scheduler import Scheduler
from goblin_king.store import SQLiteStore
from goblin_king.validation import WorkerValidationResult
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
    assert "resource_policy.concurrency_deferred" in [
        event.event_type for event in store.list_events()
    ]
