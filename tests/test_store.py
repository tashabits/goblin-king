"""Local persistence tests for Phase 1 SQLite storage."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from threading import Barrier

import pytest
from sqlalchemy.exc import IntegrityError, OperationalError

import goblin_king.store as store_module
from goblin_king.contracts import (
    DeploymentRecord,
    EventRecord,
    FanoutRecord,
    GoblinResult,
    HeartbeatRecord,
    ImagePromotionRecord,
    JobRecord,
    RunRecord,
    ScheduleRecord,
    WorkerValidationRecord,
    utc_now,
)
from goblin_king.store import SQLiteStore


def test_sqlite_store_retries_concurrent_schema_startup(tmp_path: Path, monkeypatch) -> None:
    """Verify parallel control-plane startup can tolerate a benign schema race."""
    original_create_all = store_module.metadata.create_all
    calls = {"count": 0}

    def flaky_create_all(engine):
        calls["count"] += 1
        if calls["count"] == 1:
            raise OperationalError(
                "CREATE TABLE repository_entries",
                {},
                Exception("table repository_entries already exists"),
            )
        return original_create_all(engine)

    monkeypatch.setattr(store_module.metadata, "create_all", flaky_create_all)
    monkeypatch.setattr(store_module.time, "sleep", lambda _seconds: None)

    store = SQLiteStore(tmp_path / "goblin.sqlite3")
    result = store.save_event(
        EventRecord(id="event-1", event_type="test", source="api", created_at=utc_now())
    )

    assert calls["count"] == 2
    assert result is None
    assert store.list_events()[0].id == "event-1"


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
        resource_policy={"timeout_seconds": 30, "memory": {"limit": "512Mi"}},
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
    assert loaded_run.resource_policy == {
        "timeout_seconds": 30,
        "memory": {"limit": "512Mi"},
    }


def test_sqlite_store_round_trips_worker_validation(tmp_path: Path) -> None:
    """Verify worker validation proof can be persisted and queried by image digest."""
    store = SQLiteStore(tmp_path / "goblin.sqlite3")
    record = WorkerValidationRecord(
        id="validation-1",
        kind="example.hello",
        image="example:local",
        image_digest="sha256:abc",
        contract_version="goblin-king/v1alpha1",
        validator_version="goblin-king-validator/v1",
        validated_at=utc_now(),
        status="passed",
        effective_policy={"timeout_seconds": 60},
    )

    store.save_worker_validation(record)

    loaded = store.get_latest_worker_validation(
        kind="example.hello",
        image_digest="sha256:abc",
        contract_version="goblin-king/v1alpha1",
        validator_version="goblin-king-validator/v1",
    )
    assert loaded is not None
    assert loaded.status == "passed"
    assert loaded.effective_policy == {"timeout_seconds": 60}
    assert store.latest_worker_validation_for_kind("example.hello") == loaded
    assert (
        store.get_latest_worker_validation(
            kind="example.hello",
            image_digest="sha256:def",
            contract_version="goblin-king/v1alpha1",
            validator_version="goblin-king-validator/v1",
        )
        is None
    )


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


def test_store_counts_active_jobs_by_kind(tmp_path: Path) -> None:
    """Verify concurrency policy helpers count only leased/running jobs for one kind."""
    now = utc_now()
    store = SQLiteStore(tmp_path / "goblin.sqlite3")
    jobs = [
        JobRecord(
            id="leased",
            kind="example.echo",
            input={},
            created_at=now,
            status="leased",
        ),
        JobRecord(
            id="running",
            kind="example.echo",
            input={},
            created_at=now,
            status="running",
        ),
        JobRecord(
            id="queued",
            kind="example.echo",
            input={},
            created_at=now,
            status="queued",
        ),
        JobRecord(
            id="other",
            kind="example.other",
            input={},
            created_at=now,
            status="running",
        ),
    ]
    for job in jobs:
        store.save_job(job)

    assert store.count_active_jobs("example.echo") == 2
    assert store.count_active_jobs("example.echo", exclude_job_id="leased") == 1


def test_store_counts_active_jobs_by_project(tmp_path: Path) -> None:
    """Verify project concurrency helpers count only active jobs in one project."""
    now = utc_now()
    store = SQLiteStore(tmp_path / "goblin.sqlite3")
    jobs = [
        JobRecord(
            id="leased-a",
            kind="example.echo",
            input={},
            created_at=now,
            project_id="project-a",
            status="leased",
        ),
        JobRecord(
            id="running-a",
            kind="example.other",
            input={},
            created_at=now,
            project_id="project-a",
            status="running",
        ),
        JobRecord(
            id="queued-a",
            kind="example.echo",
            input={},
            created_at=now,
            project_id="project-a",
            status="queued",
        ),
        JobRecord(
            id="running-b",
            kind="example.echo",
            input={},
            created_at=now,
            project_id="project-b",
            status="running",
        ),
        JobRecord(
            id="projectless",
            kind="example.echo",
            input={},
            created_at=now,
            status="running",
        ),
    ]
    for job in jobs:
        store.save_job(job)

    assert store.count_active_project_jobs("project-a") == 2
    assert store.count_active_project_jobs("project-a", exclude_job_id="leased-a") == 1
    assert store.count_active_project_jobs("project-b") == 1
    assert store.count_active_project_jobs(None) == 1


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


def test_simultaneous_sqlite_claims_have_exactly_one_owner(tmp_path: Path) -> None:
    """Serialize competing claim transactions without an error or duplicate lease."""
    now = utc_now()
    db_path = tmp_path / "goblin.sqlite3"
    stores = [SQLiteStore(db_path), SQLiteStore(db_path)]
    stores[0].save_job(
        JobRecord(
            id="job-simultaneous-claim",
            kind="example.echo",
            input={},
            created_at=now,
            due_at=now,
        )
    )
    ready = Barrier(2)

    def claim(store_and_owner: tuple[SQLiteStore, str]) -> list[JobRecord]:
        store, owner = store_and_owner
        ready.wait()
        return store.claim_due_jobs(
            worker_id=owner,
            now=now,
            lease_until=now + timedelta(seconds=60),
            limit=1,
        )

    candidates = [(stores[0], "scheduler-a"), (stores[1], "scheduler-b")]
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(claim, candidates))

    winners = [job for claimed in results for job in claimed]
    persisted = stores[0].get_job("job-simultaneous-claim")
    assert len(winners) == 1
    assert persisted is not None
    assert persisted.status == "leased"
    assert persisted.lease_owner == winners[0].lease_owner
    assert persisted.lease_owner in {"scheduler-a", "scheduler-b"}


def test_claim_due_jobs_recovers_an_expired_running_lease(tmp_path: Path) -> None:
    """Allow a replacement scheduler to recover work stranded after mark-running."""
    now = utc_now()
    store = SQLiteStore(tmp_path / "goblin.sqlite3")
    store.save_job(
        JobRecord(
            id="job-stranded",
            kind="example.echo",
            input={},
            created_at=now - timedelta(minutes=2),
            due_at=now - timedelta(minutes=2),
            status="running",
            attempt_count=1,
            lease_owner="dead-scheduler",
            leased_until=now - timedelta(seconds=1),
        )
    )

    recovered = store.claim_due_jobs(
        worker_id="replacement-scheduler",
        now=now,
        lease_until=now + timedelta(seconds=60),
        limit=10,
    )

    assert [job.id for job in recovered] == ["job-stranded"]
    assert recovered[0].status == "leased"
    assert recovered[0].attempt_count == 1
    assert recovered[0].lease_owner == "replacement-scheduler"


def test_lease_renewal_is_owner_scoped_and_dead_leases_remain_recoverable(
    tmp_path: Path,
) -> None:
    """Fence renewals by owner without preventing recovery after renewals stop."""
    now = utc_now()
    store = SQLiteStore(tmp_path / "goblin.sqlite3")
    store.save_job(
        JobRecord(
            id="job-renewed",
            kind="example.echo",
            input={},
            created_at=now,
            due_at=now,
        )
    )
    claimed = store.claim_due_jobs(
        worker_id="active-scheduler",
        now=now,
        lease_until=now + timedelta(seconds=1),
        limit=1,
    )
    renewed_until = now + timedelta(seconds=5)

    assert len(claimed) == 1
    assert (
        store.try_renew_job_lease(
            "job-renewed",
            expected_lease_owner="other-scheduler",
            lease_until=renewed_until,
        )
        is False
    )
    assert (
        store.try_renew_job_lease(
            "job-renewed",
            expected_lease_owner="active-scheduler",
            lease_until=renewed_until,
        )
        is True
    )
    assert (
        store.claim_due_jobs(
            worker_id="replacement-scheduler",
            now=renewed_until - timedelta(microseconds=1),
            lease_until=renewed_until + timedelta(seconds=60),
            limit=1,
        )
        == []
    )

    recovered = store.claim_due_jobs(
        worker_id="replacement-scheduler",
        now=renewed_until,
        lease_until=renewed_until + timedelta(seconds=60),
        limit=1,
    )

    assert len(recovered) == 1
    assert recovered[0].lease_owner == "replacement-scheduler"


def test_store_persists_fanout_and_job_metadata(tmp_path: Path) -> None:
    """Verify fanout records and child job metadata persist."""
    now = utc_now()
    store = SQLiteStore(tmp_path / "goblin.sqlite3")
    fanout = FanoutRecord(
        id="fanout-1",
        created_at=now,
        created_by="api",
        correlation_id="corr-1",
        description="demo",
    )

    store.save_fanout(fanout)
    store.save_job(
        JobRecord(
            id="job-1",
            kind="example.echo",
            input={},
            created_at=now,
            fanout_id="fanout-1",
            metadata={"fanout_item_index": 0},
        )
    )

    assert store.get_fanout("fanout-1") == fanout
    jobs = store.list_fanout_jobs("fanout-1")
    assert jobs[0].fanout_id == "fanout-1"
    assert jobs[0].metadata["fanout_item_index"] == 0


def test_store_persists_events_and_filters(tmp_path: Path) -> None:
    """Verify durable events persist and can be filtered by event fields."""
    now = utc_now()
    store = SQLiteStore(tmp_path / "goblin.sqlite3")
    first = EventRecord(
        id="event-1",
        created_at=now,
        event_type="job.queued",
        source="api",
        job_id="job-1",
        payload={"kind": "example.echo"},
    )
    second = EventRecord(
        id="event-2",
        created_at=now + timedelta(seconds=1),
        event_type="job.completed",
        source="scheduler",
        job_id="job-1",
        run_id="run-1",
        scheduler_id="scheduler-1",
    )

    persisted_first = store.persist_event(first)
    persisted_second = store.persist_event(second)

    events = store.list_events()
    assert [event.id for event in events] == ["event-1", "event-2"]
    assert [event.sequence for event in events] == [1, 2]
    assert persisted_first.sequence == 1
    assert persisted_second.sequence == 2
    assert [event.id for event in store.list_events(event_type="job.completed")] == ["event-2"]
    assert [event.id for event in store.list_events(after_id="event-1")] == ["event-2"]
    assert [event.id for event in store.list_events(job_id="job-1")] == ["event-1", "event-2"]


def test_store_clamps_backward_event_time_and_orders_by_durable_sequence(tmp_path: Path) -> None:
    """Preserve call order when an event producer's wall clock moves backward."""
    now = utc_now()
    store = SQLiteStore(tmp_path / "goblin.sqlite3")

    first = store.persist_event(
        EventRecord(
            id="event-later-clock",
            created_at=now,
            event_type="worker.started",
            source="runtime",
            job_id="job-1",
        )
    )
    second = store.persist_event(
        EventRecord(
            id="event-rolled-back-clock",
            created_at=now - timedelta(seconds=2),
            event_type="worker.completed",
            source="runtime",
            job_id="job-1",
        )
    )

    events = store.list_events(job_id="job-1")
    assert [event.sequence for event in events] == [1, 2]
    assert first.created_at < second.created_at
    assert events == [first, second]
    assert store.list_events(after_id=first.id) == [second]


def test_concurrent_event_writers_receive_unique_causal_sequences(tmp_path: Path) -> None:
    """Serialize event insertion across independent store connections."""
    db_path = tmp_path / "goblin.sqlite3"
    stores = [SQLiteStore(db_path) for _ in range(8)]
    timestamp = utc_now()

    def persist(index: int) -> EventRecord:
        return stores[index].persist_event(
            EventRecord(
                id=f"event-{index}",
                created_at=timestamp,
                event_type="worker.progress",
                source="worker",
                job_id="job-1",
            )
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(persist, range(8)))

    events = SQLiteStore(db_path).list_events(job_id="job-1")
    assert [event.sequence for event in events] == list(range(1, 9))
    assert all(
        left.created_at < right.created_at
        for left, right in zip(events, events[1:], strict=False)
    )


def test_event_sequence_is_not_reused_after_history_cleanup(tmp_path: Path) -> None:
    """Keep the causal counter monotonic even when retained event rows are deleted."""
    store = SQLiteStore(tmp_path / "goblin.sqlite3")
    first = store.persist_event(
        EventRecord(id="event-1", created_at=utc_now(), event_type="test", source="api")
    )
    with store.engine.begin() as connection:
        connection.execute(store_module.events_table.delete())

    second = store.persist_event(
        EventRecord(id="event-2", created_at=utc_now(), event_type="test", source="api")
    )

    assert first.sequence == 1
    assert second.sequence == 2


def test_schema_migration_repairs_historical_terminal_run_timestamps(tmp_path: Path) -> None:
    """Prevent API reads from exposing inverted or missing terminal finish timestamps."""
    db_path = tmp_path / "goblin.sqlite3"
    store = SQLiteStore(db_path)
    started_at = utc_now()
    store.save_job(
        JobRecord(id="job-legacy", kind="example.echo", input={}, created_at=started_at)
    )
    with store.engine.begin() as connection:
        connection.execute(
            store_module.runs_table.insert().values(
                id="run-legacy",
                job_id="job-legacy",
                kind="example.echo",
                attempt=1,
                status="completed",
                started_at=started_at,
                finished_at=started_at - timedelta(seconds=1),
                result_json=GoblinResult.ok().model_dump_json(),
                max_retries=0,
            )
        )
        connection.execute(
            store_module.runs_table.insert().values(
                id="run-legacy-null",
                job_id="job-legacy",
                kind="example.echo",
                attempt=2,
                status="failed",
                started_at=started_at,
                finished_at=None,
                result_json=GoblinResult.failed(error="legacy").model_dump_json(),
                max_retries=0,
            )
        )

    migrated = SQLiteStore(db_path)
    repaired = migrated.get_run("run-legacy")
    repaired_null = migrated.get_run("run-legacy-null")

    assert repaired is not None
    assert repaired.finished_at == repaired.started_at
    assert repaired_null is not None
    assert repaired_null.finished_at == repaired_null.started_at


def test_terminal_run_without_finish_is_normalized_when_persisted(tmp_path: Path) -> None:
    """Preserve the public model shape while keeping durable terminal Run reads complete."""
    store = SQLiteStore(tmp_path / "goblin.sqlite3")
    started_at = utc_now()
    store.save_job(
        JobRecord(id="job-terminal", kind="example.echo", input={}, created_at=started_at)
    )
    store.save_run(
        RunRecord(
            id="run-terminal",
            job_id="job-terminal",
            kind="example.echo",
            status="completed",
            started_at=started_at,
            result=GoblinResult.ok(),
        )
    )

    persisted = store.get_run("run-terminal")

    assert persisted is not None
    assert persisted.finished_at == persisted.started_at


def test_save_run_remains_insert_only_for_duplicate_runtime_ids(tmp_path: Path) -> None:
    """Preserve the public store contract that rejects duplicate Run identities."""
    store = SQLiteStore(tmp_path / "goblin.sqlite3")
    started_at = utc_now()
    store.save_job(
        JobRecord(id="job-duplicate", kind="example.echo", input={}, created_at=started_at)
    )
    run = RunRecord(
        id="run-duplicate",
        job_id="job-duplicate",
        kind="example.echo",
        status="completed",
        started_at=started_at,
        finished_at=started_at,
        result=GoblinResult.ok(data={"first": True}),
    )
    store.save_run(run)

    with pytest.raises(IntegrityError):
        store.save_run(run.model_copy(update={"result": GoblinResult.ok(data={"second": True})}))

    assert store.get_run(run.id) == run


def test_attempt_finalization_and_cancellation_have_one_atomic_winner(tmp_path: Path) -> None:
    """Prevent cancellation after completion from rewriting a terminal attempt."""
    store = SQLiteStore(tmp_path / "goblin.sqlite3")
    started_at = utc_now()
    store.save_job(
        JobRecord(
            id="job-finalized",
            kind="example.echo",
            input={},
            created_at=started_at,
            status="running",
            lease_owner="scheduler-a",
            attempt_count=1,
        )
    )
    run = RunRecord(
        id="run-finalized",
        job_id="job-finalized",
        kind="example.echo",
        attempt=1,
        status="completed",
        started_at=started_at,
        finished_at=started_at + timedelta(milliseconds=1),
        result=GoblinResult.ok(),
    )

    finalization = store.finalize_job_attempt(
        run,
        status="completed",
        expected_lease_owner="scheduler-a",
    )
    terminal, changed = store.try_cancel_job("job-finalized")

    assert finalization.outcome == "finalized"
    assert terminal is not None
    assert terminal.status == "completed"
    assert changed is False


def test_running_attempt_is_finalized_in_place_with_child_metadata(tmp_path: Path) -> None:
    """Keep one Run identity from pre-execution visibility through terminal result storage."""
    store = SQLiteStore(tmp_path / "goblin.sqlite3")
    started_at = utc_now()
    store.save_job(
        JobRecord(
            id="job-live",
            kind="example.echo",
            input={},
            created_at=started_at,
            status="running",
            lease_owner="scheduler-a",
            attempt_count=1,
        )
    )
    running = RunRecord(
        id="run-live",
        job_id="job-live",
        kind="example.echo",
        attempt=1,
        status="running",
        started_at=started_at,
    )
    store.start_run(running)
    terminal = running.model_copy(
        update={
            "status": "completed",
            "finished_at": started_at + timedelta(milliseconds=1),
            "result": GoblinResult.ok(
                artifacts=[
                    {
                        "name": "proof.txt",
                        "uri": "file:///proof.txt",
                        "media_type": "text/plain",
                    }
                ],
                handoff=[{"kind": "proof", "payload": {"ok": True}}],
            ),
        }
    )

    finalized = store.finalize_job_attempt(
        terminal,
        status="completed",
        expected_lease_owner="scheduler-a",
    )

    assert finalized.outcome == "finalized"
    assert finalized.run.id == running.id
    assert store.list_job_runs("job-live") == [terminal]
    assert store.list_run_artifacts(running.id)[0].name == "proof.txt"


def test_finalizing_an_existing_terminal_run_rolls_back_the_job_transition(
    tmp_path: Path,
) -> None:
    """Reject terminal rewrites without partially completing their owning job."""
    store = SQLiteStore(tmp_path / "goblin.sqlite3")
    started_at = utc_now()
    store.save_job(
        JobRecord(
            id="job-terminal-rewrite",
            kind="example.echo",
            input={},
            created_at=started_at,
            status="running",
            lease_owner="scheduler-a",
            attempt_count=1,
        )
    )
    terminal = RunRecord(
        id="run-terminal-rewrite",
        job_id="job-terminal-rewrite",
        kind="example.echo",
        attempt=1,
        status="completed",
        started_at=started_at,
        finished_at=started_at + timedelta(milliseconds=1),
        result=GoblinResult.ok(),
    )
    store.save_run(terminal)

    with pytest.raises(ValueError, match="from status 'completed'"):
        store.finalize_job_attempt(
            terminal,
            status="completed",
            expected_lease_owner="scheduler-a",
        )

    job = store.get_job("job-terminal-rewrite")
    assert job is not None
    assert job.status == "running"
    assert job.lease_owner == "scheduler-a"
    assert store.get_run(terminal.id) == terminal


@pytest.mark.parametrize("mismatch", ["job_id", "kind", "project_id", "attempt"])
def test_finalizing_a_running_run_rejects_lineage_changes_atomically(
    tmp_path: Path,
    mismatch: str,
) -> None:
    """Keep the persisted Run and job lease unchanged after a foreign finalization."""
    store = SQLiteStore(tmp_path / f"goblin-{mismatch}.sqlite3")
    started_at = utc_now()
    attempt_count = 2 if mismatch == "attempt" else 1
    source_job = JobRecord(
        id="job-lineage-source",
        kind="example.echo",
        input={},
        created_at=started_at,
        status="running",
        lease_owner="scheduler-a",
        attempt_count=attempt_count,
    )
    store.save_job(source_job)
    if mismatch == "job_id":
        store.save_job(
            source_job.model_copy(
                update={"id": "job-lineage-other", "attempt_count": 1}
            )
        )
    running = RunRecord(
        id="run-lineage",
        job_id=source_job.id,
        kind=source_job.kind,
        attempt=1,
        status="running",
        started_at=started_at,
    )
    store.start_run(running)
    terminal = running.model_copy(
        update={
            "job_id": "job-lineage-other" if mismatch == "job_id" else running.job_id,
            "kind": "example.fail" if mismatch == "kind" else running.kind,
            "project_id": "project-other" if mismatch == "project_id" else running.project_id,
            "attempt": 2 if mismatch == "attempt" else running.attempt,
            "status": "completed",
            "finished_at": started_at + timedelta(milliseconds=1),
            "result": GoblinResult.ok(),
        }
    )

    with pytest.raises(ValueError, match="cannot change Run .* lineage"):
        store.finalize_job_attempt(
            terminal,
            status="completed",
            expected_lease_owner="scheduler-a",
        )

    target_job_id = terminal.job_id
    target_job = store.get_job(target_job_id)
    assert target_job is not None
    assert target_job.status == "running"
    assert target_job.lease_owner == "scheduler-a"
    assert store.get_run(running.id) == running


def test_stale_attempt_cannot_finalize_a_newer_lease(tmp_path: Path) -> None:
    """Persist old execution evidence without changing the newer scheduler's job state."""
    store = SQLiteStore(tmp_path / "goblin.sqlite3")
    started_at = utc_now()
    store.save_job(
        JobRecord(
            id="job-released",
            kind="example.echo",
            input={},
            created_at=started_at,
            status="running",
            lease_owner="scheduler-new",
            attempt_count=2,
        )
    )
    stale_run = RunRecord(
        id="run-stale",
        job_id="job-released",
        kind="example.echo",
        attempt=1,
        status="completed",
        started_at=started_at,
        finished_at=started_at + timedelta(milliseconds=1),
        result=GoblinResult.ok(),
    )

    finalization = store.finalize_job_attempt(
        stale_run,
        status="completed",
        expected_lease_owner="scheduler-old",
    )

    current = store.get_job("job-released")
    assert finalization.outcome == "stale"
    assert current is not None
    assert current.status == "running"
    assert current.attempt_count == 2
    assert current.lease_owner == "scheduler-new"
    assert store.get_run("run-stale") == stale_run


def test_concurrent_cancellation_requests_create_one_state_transition(tmp_path: Path) -> None:
    """Allow only one caller to win a cancellation compare-and-set."""
    db_path = tmp_path / "goblin.sqlite3"
    stores = [SQLiteStore(db_path), SQLiteStore(db_path)]
    started_at = utc_now()
    stores[0].save_job(
        JobRecord(
            id="job-cancel-once",
            kind="example.echo",
            input={},
            created_at=started_at,
            status="running",
            lease_owner="scheduler-a",
            attempt_count=1,
        )
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda store: store.try_cancel_job("job-cancel-once"), stores))

    assert sum(1 for _job, changed in results if changed) == 1
    assert all(job is not None and job.status == "cancelled" for job, _changed in results)


def test_schema_migration_backfills_stable_sequences_for_legacy_events(tmp_path: Path) -> None:
    """Give pre-sequence event rows their original insertion order during upgrade."""
    db_path = tmp_path / "legacy.sqlite3"
    now = utc_now()
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE events (
                id TEXT PRIMARY KEY,
                created_at DATETIME NOT NULL,
                event_type TEXT NOT NULL,
                source TEXT NOT NULL,
                project_id TEXT,
                job_id TEXT,
                run_id TEXT,
                fanout_id TEXT,
                schedule_id TEXT,
                worker_id TEXT,
                scheduler_id TEXT,
                payload_json TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
        connection.executemany(
            "INSERT INTO events (id, created_at, event_type, source) VALUES (?, ?, ?, ?)",
            [
                ("event-first", now.isoformat(), "worker.started", "runtime"),
                (
                    "event-second",
                    (now - timedelta(seconds=1)).isoformat(),
                    "worker.completed",
                    "runtime",
                ),
            ],
        )

    events = SQLiteStore(db_path).list_events()
    with sqlite3.connect(db_path) as connection:
        delivery_table = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name = 'event_stream_deliveries'"
        ).fetchone()

    assert [event.id for event in events] == ["event-first", "event-second"]
    assert [event.sequence for event in events] == [1, 2]
    assert delivery_table == ("event_stream_deliveries",)


def test_store_upserts_heartbeats(tmp_path: Path) -> None:
    """Verify heartbeat owners update in place instead of duplicating rows."""
    now = utc_now()
    store = SQLiteStore(tmp_path / "goblin.sqlite3")
    store.upsert_heartbeat(
        HeartbeatRecord(
            owner_id="scheduler-1",
            owner_type="scheduler",
            status="starting",
            last_seen_at=now,
        )
    )
    store.upsert_heartbeat(
        HeartbeatRecord(
            owner_id="scheduler-1",
            owner_type="scheduler",
            status="running",
            last_seen_at=now + timedelta(seconds=1),
            payload={"runtime": "docker"},
        )
    )

    heartbeats = store.list_heartbeats()

    assert len(heartbeats) == 1
    assert heartbeats[0].status == "running"
    assert heartbeats[0].payload == {"runtime": "docker"}
    assert store.get_heartbeat("scheduler-1") == heartbeats[0]


def test_image_promotion_and_deployment_records_persist(tmp_path: Path) -> None:
    """Verify deployment proof records survive a SQLite round trip."""
    store = SQLiteStore(tmp_path / "goblin.sqlite3")
    now = utc_now()
    promotion = ImagePromotionRecord(
        id="promo-1",
        kind="example.hello",
        source_image="example:local",
        target_image="registry.example/example:prod",
        status="planned",
        actor="test",
        created_at=now,
        updated_at=now,
        detail={"commands": [["docker", "push", "registry.example/example:prod"]]},
    )
    deployment = DeploymentRecord(
        id="deploy-1",
        name="goblin-king",
        action="helm-template",
        status="planned",
        actor="test",
        command=["helm", "template", "goblin-king", "charts/goblin-king"],
        created_at=now,
        updated_at=now,
        detail={"execute": False},
    )

    store.save_image_promotion(promotion)
    store.save_deployment_record(deployment)
    updated = store.update_image_promotion(
        "promo-1",
        status="promoted",
        digest="sha256:abc",
        detail={"marked_by": "test"},
        updated_at=utc_now(),
    )

    assert updated is not None
    assert updated.status == "promoted"
    assert updated.digest == "sha256:abc"
    assert store.list_image_promotions()[0].target_image == "registry.example/example:prod"
    assert store.list_deployment_records()[0].command[0] == "helm"
