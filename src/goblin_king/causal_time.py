"""Provide monotonic UTC presentation time and explicit causal timestamp floors."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from threading import Lock

MINIMUM_CAUSAL_STEP = timedelta(microseconds=1)


def ensure_utc(value: datetime) -> datetime:
    """Normalize naive or offset timestamps to timezone-aware UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class MonotonicUtcClock:
    """Clamp a process-local wall clock so successive UTC values never move backward."""

    def __init__(self, wall_clock: Callable[[], datetime] | None = None) -> None:
        self._wall_clock = wall_clock or (lambda: datetime.now(UTC))
        self._last: datetime | None = None
        self._lock = Lock()

    def now(self) -> datetime:
        """Return wall time or one microsecond after the preceding process-local value."""
        candidate = ensure_utc(self._wall_clock())
        with self._lock:
            if self._last is not None and candidate <= self._last:
                candidate = self._last + MINIMUM_CAUSAL_STEP
            self._last = candidate
            return candidate


_SYSTEM_CLOCK = MonotonicUtcClock()


def monotonic_utc_now() -> datetime:
    """Return the process-wide monotonic UTC presentation timestamp."""
    return _SYSTEM_CLOCK.now()


def causally_after(
    *predecessors: datetime | None,
    candidate: datetime | None = None,
) -> datetime:
    """Return a UTC timestamp strictly after every supplied causal predecessor."""
    current = ensure_utc(candidate) if candidate is not None else monotonic_utc_now()
    normalized = [ensure_utc(value) for value in predecessors if value is not None]
    if not normalized:
        return current
    floor = max(normalized)
    return current if current > floor else floor + MINIMUM_CAUSAL_STEP
