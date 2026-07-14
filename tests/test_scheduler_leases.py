"""Focused tests for active scheduler lease maintenance."""

from __future__ import annotations

import time
from datetime import datetime
from threading import Event

from goblin_king import scheduler_leases
from goblin_king.scheduler_leases import ActiveJobLease


class BlockingLeaseStore:
    """Hold one renewal call so cleanup can prove its wait is bounded."""

    def __init__(self) -> None:
        self.entered = Event()
        self.release = Event()

    def try_renew_job_lease(
        self,
        _job_id: str,
        *,
        expected_lease_owner: str,
        lease_until: datetime,
    ) -> bool:
        assert expected_lease_owner == "scheduler-a"
        assert isinstance(lease_until, datetime)
        self.entered.set()
        assert self.release.wait(5)
        return True


def test_active_lease_cleanup_does_not_wait_forever_for_a_store_write(monkeypatch) -> None:
    """Bound scheduler cleanup even when a renewal write is still blocked."""
    store = BlockingLeaseStore()
    monkeypatch.setattr(scheduler_leases, "LEASE_THREAD_JOIN_SECONDS", 0.01)
    lease = ActiveJobLease(
        store=store,
        job_id="job-active",
        lease_owner="scheduler-a",
        lease_seconds=1,
    )

    started_at = time.monotonic()
    with lease:
        assert store.entered.wait(1)
    elapsed = time.monotonic() - started_at

    assert elapsed < 0.5
    store.release.set()
    lease._thread.join(1)
    assert not lease._thread.is_alive()
