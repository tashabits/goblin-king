"""Keep scheduler-owned job leases alive while synchronous attempts are active."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from threading import Event, Thread
from types import TracebackType
from typing import Protocol

from goblin_king.contracts import utc_now

LEASE_THREAD_JOIN_SECONDS = 5.0
LOGGER = logging.getLogger(__name__)


class JobLeaseStore(Protocol):
    """Describe the narrow persistence operation required by lease maintenance."""

    def try_renew_job_lease(
        self,
        job_id: str,
        *,
        expected_lease_owner: str,
        lease_until: datetime,
    ) -> bool: ...


class ActiveJobLease:
    """Renew one owned lease until its scheduler attempt leaves the active scope."""

    def __init__(
        self,
        *,
        store: JobLeaseStore,
        job_id: str,
        lease_owner: str,
        lease_seconds: int,
    ) -> None:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be greater than zero")
        self.store = store
        self.job_id = job_id
        self.lease_owner = lease_owner
        self.lease_seconds = lease_seconds
        self._renew_interval = min(max(lease_seconds / 3, 0.1), 30.0)
        self._stop = Event()
        self._thread = Thread(
            target=self._renew_loop,
            name=f"goblin-lease-{job_id}",
            daemon=True,
        )

    def __enter__(self) -> ActiveJobLease:
        self._thread.start()
        return self

    def __exit__(
        self,
        _exception_type: type[BaseException] | None,
        _exception: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        self._stop.set()
        # SQLite's busy timeout is finite, but lease cleanup must never become a new
        # unbounded scheduler shutdown path. The daemon can finish an in-flight write;
        # its owner/status predicate makes any late write harmless.
        self._thread.join(timeout=LEASE_THREAD_JOIN_SECONDS)

    def _renew_loop(self) -> None:
        """Renew immediately and periodically, stopping after ownership is lost."""
        while not self._stop.is_set():
            lease_until = utc_now() + timedelta(seconds=self.lease_seconds)
            try:
                renewed = self.store.try_renew_job_lease(
                    self.job_id,
                    expected_lease_owner=self.lease_owner,
                    lease_until=lease_until,
                )
            except Exception:
                # A transient store failure must not terminate the worker attempt. Retry before
                # the next renewal boundary; persistent failure naturally leaves the lease
                # recoverable by another scheduler after its last successful deadline.
                LOGGER.exception(
                    "job lease renewal failed; retrying",
                    extra={"job_id": self.job_id, "lease_owner": self.lease_owner},
                )
                renewed = True
            if not renewed:
                return
            self._stop.wait(self._renew_interval)
