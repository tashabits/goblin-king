"""Local contract tests for the Phase 1 public goblin models."""

from pydantic import ValidationError

from goblin_king.contracts import GoblinDefinition, GoblinResult


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
