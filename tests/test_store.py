"""Local persistence tests for Phase 1 SQLite storage."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path

from sqlalchemy.exc import OperationalError

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
    store.save_event(
        EventRecord(id="event-1", event_type="test", source="api", created_at=utc_now())
    )

    assert calls["count"] == 2
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

    persisted_first = store.save_event(first)
    persisted_second = store.save_event(second)

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

    first = store.save_event(
        EventRecord(
            id="event-later-clock",
            created_at=now,
            event_type="worker.started",
            source="runtime",
            job_id="job-1",
        )
    )
    second = store.save_event(
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
        return stores[index].save_event(
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
    first = store.save_event(
        EventRecord(id="event-1", created_at=utc_now(), event_type="test", source="api")
    )
    with store.engine.begin() as connection:
        connection.execute(store_module.events_table.delete())

    second = store.save_event(
        EventRecord(id="event-2", created_at=utc_now(), event_type="test", source="api")
    )

    assert first.sequence == 1
    assert second.sequence == 2


def test_schema_migration_repairs_historical_inverted_run_timestamps(tmp_path: Path) -> None:
    """Prevent API reads from exposing an already-persisted future start time."""
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

    repaired = SQLiteStore(db_path).get_run("run-legacy")

    assert repaired is not None
    assert repaired.finished_at == repaired.started_at


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

    assert [event.id for event in events] == ["event-first", "event-second"]
    assert [event.sequence for event in events] == [1, 2]


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
