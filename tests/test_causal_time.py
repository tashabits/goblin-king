"""Tests for process-local monotonic UTC and causal timestamp floors."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

from goblin_king.causal_time import MonotonicUtcClock, causally_after


def test_monotonic_clock_clamps_wall_clock_rollback_and_ties() -> None:
    """Advance by one microsecond when the wall clock repeats or moves backward."""
    start = datetime(2026, 7, 12, 12, 0, tzinfo=UTC)
    readings = iter([start, start - timedelta(seconds=1), start - timedelta(seconds=1)])
    clock = MonotonicUtcClock(lambda: next(readings))

    observed = [clock.now(), clock.now(), clock.now()]

    assert observed == [
        start,
        start + timedelta(microseconds=1),
        start + timedelta(microseconds=2),
    ]


def test_causally_after_normalizes_offsets_and_advances_past_every_predecessor() -> None:
    """Use the newest causal floor even when candidates use another timezone."""
    predecessor = datetime(2026, 7, 12, 12, 0, tzinfo=UTC)
    later = predecessor + timedelta(seconds=1)
    candidate = datetime(2026, 7, 12, 5, 0, tzinfo=timezone(timedelta(hours=-7)))

    observed = causally_after(predecessor, later, candidate=candidate)

    assert observed == later + timedelta(microseconds=1)
    assert observed.tzinfo == UTC
