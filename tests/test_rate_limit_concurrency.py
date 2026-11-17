"""Rate-limit windows must count concurrent authenticated requests exactly once."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest
from sqlalchemy import select

from goblin_king import auth
from goblin_king.contracts import utc_now
from goblin_king.store import SQLiteStore
from goblin_king.store_schema import rate_limits_table


@pytest.mark.parametrize("expired", [None, False, True])
def test_concurrent_rate_limit_admission_and_window_reset(tmp_path, monkeypatch, expired):
    now = utc_now()
    monkeypatch.setattr(auth, "utc_now", lambda: now)
    stores = [SQLiteStore(tmp_path / "shared.sqlite3") for _ in range(24)]
    principal = auth.Principal(user_id="admin", token_id="shared", role="admin")
    if expired is not None:
        auth.check_rate_limit(stores[0], principal=principal, route="/runs", max_per_minute=5)
    if expired:
        now += timedelta(seconds=60)
    barrier = Barrier(len(stores))

    def request(store):
        barrier.wait(timeout=10)
        try:
            auth.check_rate_limit(store, principal=principal, route="/runs", max_per_minute=5)
        except auth.RateLimitExceeded:
            return False
        return True

    try:
        with ThreadPoolExecutor(max_workers=len(stores)) as pool:
            allowed = list(pool.map(request, stores))
        assert sum(allowed) == (4 if expired is False else 5)
        with stores[0].engine.connect() as connection:
            row = connection.execute(select(rate_limits_table)).mappings().one()
        assert row["count"] == len(stores) + (1 if expired is False else 0)
        assert row["window_started_at"].replace(tzinfo=now.tzinfo) == now
        assert len(stores[0].list_audit_logs()) == len(stores) - sum(allowed)
    finally:
        for store in stores:
            store.engine.dispose()
