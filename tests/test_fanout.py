"""Tests for shared fanout and retry behavior."""

from __future__ import annotations

from goblin_king.contracts import JobRecord, utc_now
from goblin_king.fanout import (
    FanoutCreateRequest,
    RetryCreateRequest,
    create_fanout,
    derive_fanout_status,
    retry_job,
)
from goblin_king.registry import GoblinRegistry
from goblin_king.store import SQLiteStore


def test_create_fanout_validates_and_creates_child_jobs(tmp_path) -> None:
    """Verify fanout creation persists metadata and child jobs."""
    store = SQLiteStore(tmp_path / "goblin.sqlite3")
    registry = GoblinRegistry.from_path("examples/goblins.json")

    detail = create_fanout(
        store=store,
        registry=registry,
        request=FanoutCreateRequest(
            description="demo",
            items=[
                {"kind": "example.echo", "input": {"message": "one"}},
                {"kind": "example.echo", "input": {"message": "two"}, "priority": 200},
            ],
        ),
        created_by="api",
    )

    assert detail.fanout.description == "demo"
    assert detail.status == "queued"
    assert detail.counts["total"] == 2
    assert detail.jobs[0].metadata["fanout_item_index"] == 0
    assert detail.jobs[1].priority == 200


def test_derive_fanout_status_variants() -> None:
    """Verify derived fanout status covers queued, running, completed, failed, and partial."""
    now = utc_now()

    def job(status: str) -> JobRecord:
        return JobRecord(id=status, kind="example.echo", input={}, created_at=now, status=status)

    assert derive_fanout_status([job("queued")]) == "queued"
    assert derive_fanout_status([job("running")]) == "running"
    assert derive_fanout_status([job("completed"), job("completed")]) == "completed"
    assert derive_fanout_status([job("failed"), job("timed_out")]) == "failed"
    assert derive_fanout_status([job("completed"), job("failed")]) == "partial"


def test_retry_job_creates_new_queued_job_for_terminal_source(tmp_path) -> None:
    """Verify retry creates a new queued job with lineage metadata."""
    store = SQLiteStore(tmp_path / "goblin.sqlite3")
    source = JobRecord(
        id="source",
        kind="example.echo",
        input={"message": "old"},
        created_at=utc_now(),
        status="failed",
        correlation_id="corr",
        fanout_id="fanout",
    )
    store.save_job(source)

    retry = retry_job(
        store=store,
        job_id="source",
        request=RetryCreateRequest(reason="try again", input={"message": "new"}),
        created_by="api-retry",
    )

    assert retry.id != "source"
    assert retry.status == "queued"
    assert retry.input == {"message": "new"}
    assert retry.correlation_id == "corr"
    assert retry.fanout_id == "fanout"
    assert retry.metadata["retry"]["source_job_id"] == "source"


def test_retry_job_rejects_non_terminal_source(tmp_path) -> None:
    """Verify retry rejects live jobs."""
    store = SQLiteStore(tmp_path / "goblin.sqlite3")
    store.save_job(
        JobRecord(id="source", kind="example.echo", input={}, created_at=utc_now(), status="queued")
    )

    try:
        retry_job(
            store=store,
            job_id="source",
            request=RetryCreateRequest(reason="nope"),
            created_by="api-retry",
        )
    except ValueError as error:
        assert "not terminal" in str(error)
    else:  # pragma: no cover - assertion branch
        raise AssertionError("retry should reject queued jobs")
