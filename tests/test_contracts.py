"""Local contract tests for the Phase 1 public goblin models."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from goblin_king.contracts import EventRecord, GoblinDefinition, GoblinResult, RunRecord


def test_goblin_result_ok_serializes_nested_metadata() -> None:
    """Verify successful result envelopes retain artifacts, metrics, and handoffs."""
    result = GoblinResult.ok(
        data={"value": 1},
        artifacts=[{"name": "stdout", "uri": "file:///tmp/stdout.log", "media_type": "text/plain"}],
        metrics={"items": 1},
        handoff=[{"kind": "scribe.store", "payload": {"id": "abc"}}],
    )

    assert result.status == "success"
    assert result.error is None
    assert result.model_dump()["artifacts"][0]["name"] == "stdout"
    assert result.model_dump()["handoff"][0]["payload"] == {"id": "abc"}


def test_goblin_result_failed_sets_error() -> None:
    """Verify failed result envelopes preserve the explicit error string."""
    result = GoblinResult.failed(error="boom")

    assert result.status == "failed"
    assert result.error == "boom"


def test_goblin_definition_rejects_invalid_kind() -> None:
    """Ensure goblin kinds stay stable and shell-friendly."""
    try:
        GoblinDefinition(kind="Bad Kind", display_name="Bad", module="bad")
    except ValidationError as error:
        assert "kind must use lowercase" in str(error)
    else:  # pragma: no cover - assertion branch
        raise AssertionError("invalid goblin kind was accepted")


def test_run_contract_rejects_inverted_terminal_timestamps() -> None:
    """Prevent newly created Runs from exposing completion before their start."""
    started_at = datetime(2026, 7, 12, 12, 0, tzinfo=UTC)

    with pytest.raises(ValidationError, match="finished_at must be greater"):
        RunRecord(
            id="run-1",
            job_id="job-1",
            kind="example.echo",
            status="completed",
            started_at=started_at,
            finished_at=started_at - timedelta(seconds=1),
        )


def test_pre_sequence_event_payload_remains_compatible() -> None:
    """Keep older EventRecord construction valid while durable storage assigns a sequence."""
    event = EventRecord.model_validate(
        {
            "id": "legacy-event",
            "created_at": "2026-07-12T12:00:00Z",
            "event_type": "job.queued",
            "source": "api",
        }
    )

    assert event.sequence == 0


def test_terminal_run_model_keeps_nullable_finish_for_legacy_callers() -> None:
    """Keep the public model shape stable; persistence normalizes terminal null finishes."""
    run = RunRecord(
        id="legacy-run",
        job_id="legacy-job",
        kind="example.echo",
        status="completed",
        started_at=datetime(2026, 7, 12, 12, 0, tzinfo=UTC),
    )

    assert run.finished_at is None
