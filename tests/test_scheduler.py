"""Local scheduler tests for schedule materialization, leasing, retries, and timeouts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from goblin_king.contracts import ScheduleRecord
from goblin_king.registry import GoblinRegistry
from goblin_king.scheduler import Scheduler
from goblin_king.store import SQLiteStore


def build_scheduler(tmp_path: Path) -> tuple[Scheduler, SQLiteStore]:
    """Create a scheduler with a fresh SQLite store and the example registry."""
    store = SQLiteStore(tmp_path / "goblin.sqlite3")
    scheduler = Scheduler(
        registry=GoblinRegistry.from_path("examples/goblins.json"),
        store=store,
        worker_id="test-worker",
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
