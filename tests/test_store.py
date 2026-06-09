"""Local persistence tests for Phase 1 SQLite storage."""

from pathlib import Path

from goblin_king.contracts import GoblinResult, JobRecord, RunRecord, utc_now
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
