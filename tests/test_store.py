"""Local persistence tests for Phase 1 SQLite storage."""

from datetime import timedelta
from pathlib import Path

from goblin_king.contracts import (
    GoblinResult,
    JobRecord,
    RunRecord,
    ScheduleRecord,
    utc_now,
)
from goblin_king.store import SQLiteStore


def test_sqlite_store_creates_schema_and_round_trips_completed_run(tmp_path: Path) -> None:
    """Verify completed jobs and runs are persisted in a fresh SQLite database."""
    store = SQLiteStore(tmp_path / "goblin.sqlite3")
    job = JobRecord(id="job-1", kind="example.echo", input={"message": "hi"}, created_at=utc_now())
    run = RunRecord(
        id="run-1",
        job_id="job-1",
        kind="example.echo",
        status="completed",
        started_at=utc_now(),
        finished_at=utc_now(),
        result=GoblinResult.ok(
            data={"ok": True},
            artifacts=[{"name": "stdout", "uri": "file:///stdout.log"}],
            handoff=[{"kind": "scribe.store", "payload": {"run": "run-1"}}],
        ),
        timeout_seconds=30,
        max_retries=2,
        leased_until=utc_now() + timedelta(seconds=60),
    )

    store.save_job(job)
    store.save_run(run)

    loaded_job = store.get_job("job-1")
    loaded_run = store.get_run("run-1")
    assert loaded_job is not None
    assert loaded_job.input == {"message": "hi"}
    assert loaded_run is not None
    assert loaded_run.status == "completed"
    assert loaded_run.result is not None
    assert loaded_run.result.data == {"ok": True}
    assert loaded_run.timeout_seconds == 30
    assert loaded_run.max_retries == 2
    assert loaded_run.leased_until is not None


def test_sqlite_store_round_trips_failed_run(tmp_path: Path) -> None:
    """Verify failed runs preserve both status and error text."""
    store = SQLiteStore(tmp_path / "goblin.sqlite3")
    job = JobRecord(id="job-2", kind="example.fail", input={}, created_at=utc_now())
    run = RunRecord(
        id="run-2",
        job_id="job-2",
        kind="example.fail",
        status="failed",
        started_at=utc_now(),
        finished_at=utc_now(),
        result=GoblinResult.failed(error="boom"),
        error="boom",
    )

    store.save_job(job)
    store.save_run(run)

    loaded_run = store.get_run("run-2")
    assert loaded_run is not None
    assert loaded_run.status == "failed"
    assert loaded_run.error == "boom"
    assert loaded_run.result is not None
    assert loaded_run.result.error == "boom"


def test_store_creates_and_lists_due_schedules(tmp_path: Path) -> None:
    """Verify schedules persist and due schedule filtering honors enabled and future rows."""
    now = utc_now()
    store = SQLiteStore(tmp_path / "goblin.sqlite3")
    due = ScheduleRecord(
        id="schedule-due",
        kind="example.echo",
        input={"message": "due"},
        cron="* * * * *",
        created_at=now,
        next_run_at=now,
    )
    disabled = ScheduleRecord(
        id="schedule-disabled",
        kind="example.echo",
        input={},
        cron="* * * * *",
        enabled=False,
        created_at=now,
        next_run_at=now,
    )
    future = ScheduleRecord(
        id="schedule-future",
        kind="example.echo",
        input={},
        cron="* * * * *",
        created_at=now,
        next_run_at=now + timedelta(minutes=5),
    )

    store.save_schedule(due)
    store.save_schedule(disabled)
    store.save_schedule(future)

    assert [schedule.id for schedule in store.list_due_schedules(now)] == ["schedule-due"]
    assert len(store.list_schedules()) == 3


def test_claim_due_jobs_once_and_reclaim_after_lease_expiry(tmp_path: Path) -> None:
    """Verify leasing prevents duplicate claims until the lease expires."""
    now = utc_now()
    store = SQLiteStore(tmp_path / "goblin.sqlite3")
    store.save_job(
        JobRecord(
            id="job-claim",
            kind="example.echo",
            input={},
            created_at=now,
            due_at=now,
        )
    )

    first = store.claim_due_jobs(
        worker_id="worker-a",
        now=now,
        lease_until=now + timedelta(seconds=60),
        limit=10,
    )
    second = store.claim_due_jobs(
        worker_id="worker-b",
        now=now,
        lease_until=now + timedelta(seconds=60),
        limit=10,
    )
    expired = store.claim_due_jobs(
        worker_id="worker-c",
        now=now + timedelta(seconds=61),
        lease_until=now + timedelta(seconds=120),
        limit=10,
    )

    assert [job.id for job in first] == ["job-claim"]
    assert second == []
    assert [job.id for job in expired] == ["job-claim"]
    assert expired[0].lease_owner == "worker-c"
