"""Tests for event publishing transports."""

from __future__ import annotations

import json
from pathlib import Path

from goblin_king.events import DEFAULT_EVENT_STREAM, EventBus, stream_status
from goblin_king.store import SQLiteStore


def test_event_bus_writes_sqlite_pubsub_and_redis_stream(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Verify one event is durable in SQLite and mirrored to both Redis transports."""
    calls: dict[str, list] = {"publish": [], "xadd": []}

    class FakeRedis:
        def publish(self, channel: str, payload: str) -> None:
            calls["publish"].append((channel, json.loads(payload)))

        def xadd(self, stream: str, fields: dict, **kwargs) -> None:
            calls["xadd"].append((stream, fields, kwargs))

    monkeypatch.setattr("goblin_king.events.Redis.from_url", lambda _url: FakeRedis())
    store = SQLiteStore(tmp_path / "events.sqlite3")
    bus = EventBus(store=store, redis_url="redis://example/0")

    event = bus.emit("job.completed", source="cli", job_id="job-1", payload={"ok": True})

    assert store.list_events()[0].id == event.id
    assert calls["publish"][0][1]["event_type"] == "job.completed"
    assert calls["xadd"][0][0] == DEFAULT_EVENT_STREAM
    assert json.loads(calls["xadd"][0][1]["event"])["job_id"] == "job-1"


def test_stream_status_reports_group_pending_counts(monkeypatch) -> None:
    """Verify Redis Stream health metadata is normalized for API and CLI callers."""

    class FakeRedis:
        def xinfo_stream(self, _stream: str) -> dict:
            return {"length": 4, "last-generated-id": b"4-0"}

        def xinfo_groups(self, _stream: str) -> list[dict]:
            return [
                {b"name": b"workers", b"pending": 2},
                {"name": "admin", "pending": 1},
            ]

    monkeypatch.setattr("goblin_king.events.Redis.from_url", lambda _url: FakeRedis())

    status = stream_status("redis://example/0")

    assert status["ok"] is True
    assert status["length"] == 4
    assert status["pending"] == 3
    assert status["last_generated_id"] == "4-0"
    assert status["groups"][0]["name"] == "workers"
