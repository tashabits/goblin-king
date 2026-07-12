"""Tests for event publishing transports."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from redis.exceptions import RedisError, ResponseError

from goblin_king.events import EventBus, _redis_client, stream_status
from goblin_king.store import SQLiteStore
from goblin_king.store_schema import events_table


class MemoryRedis:
    """Small deterministic Redis Stream double with explicit-ID behavior."""

    def __init__(self) -> None:
        self.entries: list[tuple[str, dict[str, str]]] = []
        self.attempted_ids: list[str] = []
        self.published: list[tuple[str, dict[str, Any]]] = []
        self.fail_ids: set[str] = set()
        self.ambiguous_ids: set[str] = set()
        self.operations: list[str] = []

    def publish(self, channel: str, payload: str) -> None:
        self.operations.append("publish")
        self.published.append((channel, json.loads(payload)))

    def xrevrange(self, _stream: str, *, count: int) -> list[tuple[str, dict[str, str]]]:
        return list(reversed(self.entries[-count:]))

    def xrange(
        self,
        _stream: str,
        *,
        min: str,
        max: str,
        count: int,
    ) -> list[tuple[str, dict[str, str]]]:
        return [entry for entry in self.entries if min <= entry[0] <= max][:count]

    def xadd(self, _stream: str, fields: dict[str, str], **kwargs: Any) -> str:
        stream_id = str(kwargs["id"])
        self.attempted_ids.append(stream_id)
        self.operations.append(f"xadd:{stream_id}")
        if stream_id in self.fail_ids:
            raise RedisError(f"unavailable before {stream_id}")
        if self.entries and _stream_id(stream_id) <= _stream_id(self.entries[-1][0]):
            raise ResponseError("stream ID is equal or smaller than the target stream top item")
        self.entries.append((stream_id, fields))
        if stream_id in self.ambiguous_ids:
            self.ambiguous_ids.remove(stream_id)
            raise RedisError(f"connection lost after {stream_id}")
        return stream_id


def _stream_id(value: str) -> tuple[int, int]:
    milliseconds, sequence = value.split("-", 1)
    return int(milliseconds), int(sequence)


def test_event_bus_writes_sqlite_pubsub_and_redis_stream(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Verify one event is durable in SQLite and mirrored to both Redis transports."""
    redis = MemoryRedis()
    monkeypatch.setattr("goblin_king.events._redis_client", lambda _url: redis)
    store = SQLiteStore(tmp_path / "events.sqlite3")
    bus = EventBus(store=store, redis_url="redis://example/0")

    event = bus.emit("job.completed", source="cli", job_id="job-1", payload={"ok": True})

    assert store.list_events()[0].id == event.id
    assert event.sequence == 1
    assert redis.published[0][1]["sequence"] == 1
    assert redis.published[0][1]["event_type"] == "job.completed"
    assert redis.entries[0][0] == "1-0"
    assert json.loads(redis.entries[0][1]["event"])["job_id"] == "job-1"
    assert redis.operations == ["xadd:1-0", "publish"]


def test_event_redis_clients_bound_connect_and_command_waits(monkeypatch) -> None:
    """Keep event delivery from holding SQLite ordering indefinitely on a dead Redis."""
    captured: dict[str, Any] = {}

    def build_client(redis_url: str, **kwargs: Any) -> object:
        captured.update({"redis_url": redis_url, **kwargs})
        return object()

    monkeypatch.setattr("goblin_king.events.Redis.from_url", build_client)

    _redis_client("redis://example/0")

    assert captured == {
        "redis_url": "redis://example/0",
        "socket_connect_timeout": 1.0,
        "socket_timeout": 1.0,
    }


def test_two_event_buses_backfill_a_delayed_predecessor_before_their_event(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Reproduce two processes reaching Redis in the opposite order of durable writes."""
    redis = MemoryRedis()
    monkeypatch.setattr("goblin_king.events._redis_client", lambda _url: redis)
    db_path = tmp_path / "events.sqlite3"
    first_bus = EventBus(store=SQLiteStore(db_path), redis_url="redis://example/0")
    second_bus = EventBus(store=SQLiteStore(db_path), redis_url="redis://example/0")
    delayed_payload: dict[str, Any] = {}
    append_first = first_bus._append_stream
    monkeypatch.setattr(first_bus, "_append_stream", delayed_payload.update)

    first = first_bus.emit("job.running", source="scheduler", job_id="job-1")
    second = second_bus.emit("job.completed", source="scheduler", job_id="job-1")
    append_first(delayed_payload)

    assert (first.sequence, second.sequence) == (1, 2)
    assert [entry[0] for entry in redis.entries] == ["1-0", "2-0"]
    assert [json.loads(entry[1]["event"])["sequence"] for entry in redis.entries] == [1, 2]


def test_stream_failure_blocks_every_later_sequence_until_recovery(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Never leap over an unavailable predecessor when another process emits later work."""
    redis = MemoryRedis()
    redis.fail_ids.add("1-0")
    monkeypatch.setattr("goblin_king.events._redis_client", lambda _url: redis)
    db_path = tmp_path / "events.sqlite3"
    first_bus = EventBus(store=SQLiteStore(db_path), redis_url="redis://example/0")
    second_bus = EventBus(store=SQLiteStore(db_path), redis_url="redis://example/0")

    first_bus.emit("job.running", source="scheduler")
    second = second_bus.emit("job.completed", source="scheduler")

    assert redis.entries == []
    assert redis.attempted_ids == ["1-0", "1-0"]
    assert "2-0" not in redis.attempted_ids

    redis.fail_ids.clear()
    second_bus._append_stream(second.model_dump(mode="json"))

    assert [entry[0] for entry in redis.entries] == ["1-0", "2-0"]


def test_cleanup_gap_advances_to_the_first_retained_event(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Treat a missing committed sequence as cleanup instead of blocking forever."""
    redis = MemoryRedis()
    monkeypatch.setattr("goblin_king.events._redis_client", lambda _url: redis)
    store = SQLiteStore(tmp_path / "events.sqlite3")
    bus = EventBus(store=store, redis_url="redis://example/0")
    bus.emit("job.running", source="scheduler")
    redis.fail_ids.add("2-0")
    failed = bus.emit("worker.completed", source="runtime")
    with store.engine.begin() as connection:
        connection.execute(events_table.delete().where(events_table.c.id == failed.id))

    redis.fail_ids.clear()
    third = bus.emit("job.completed", source="scheduler")

    assert third.sequence == 3
    assert [entry[0] for entry in redis.entries] == ["1-0", "3-0"]
    assert [json.loads(entry[1]["event"])["sequence"] for entry in redis.entries] == [1, 3]


def test_one_recovery_emit_drains_more_than_one_delivery_batch(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Reach the target event even when a restart leaves over one batch pending."""
    redis = MemoryRedis()
    monkeypatch.setattr("goblin_king.events._redis_client", lambda _url: redis)
    db_path = tmp_path / "events.sqlite3"
    delayed_bus = EventBus(store=SQLiteStore(db_path), redis_url="redis://example/0")
    monkeypatch.setattr(delayed_bus, "_append_stream", lambda _payload: None)
    for index in range(105):
        delayed_bus.emit(f"test.pending.{index}", source="cli")

    recovery_bus = EventBus(store=SQLiteStore(db_path), redis_url="redis://example/0")
    target = recovery_bus.emit("test.recovery", source="cli")

    assert target.sequence == 106
    assert len(redis.entries) == 106
    assert redis.entries[0][0] == "1-0"
    assert redis.entries[-1][0] == "106-0"


def test_restart_replays_an_ambiguous_append_without_duplicate_entries(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Use the deterministic Stream ID when acknowledgement is lost after XADD."""
    redis = MemoryRedis()
    monkeypatch.setattr("goblin_king.events._redis_client", lambda _url: redis)
    db_path = tmp_path / "events.sqlite3"
    bus = EventBus(store=SQLiteStore(db_path), redis_url="redis://example/0")
    bus.emit("job.running", source="scheduler")
    redis.ambiguous_ids.add("2-0")
    bus.emit("worker.completed", source="runtime")

    restarted = EventBus(store=SQLiteStore(db_path), redis_url="redis://example/0")
    restarted.emit("job.completed", source="scheduler")

    assert [entry[0] for entry in redis.entries] == ["1-0", "2-0", "3-0"]
    assert redis.attempted_ids.count("2-0") == 2
    assert [json.loads(entry[1]["event"])["sequence"] for entry in redis.entries] == [1, 2, 3]


def test_legacy_stream_id_is_migrated_once_and_offset_survives_restart(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Continue an auto-ID stream without replaying its last durable legacy event."""
    redis = MemoryRedis()
    monkeypatch.setattr("goblin_king.events._redis_client", lambda _url: redis)
    db_path = tmp_path / "events.sqlite3"
    bus = EventBus(store=SQLiteStore(db_path), redis_url="redis://example/0")
    pending: dict[str, Any] = {}
    monkeypatch.setattr(bus, "_append_stream", pending.update)
    first = bus.emit("job.running", source="scheduler")
    legacy_payload = first.model_dump(mode="json")
    legacy_payload.pop("sequence")
    redis.entries.append(("1700000000000-0", {"event": json.dumps(legacy_payload)}))

    second_bus = EventBus(store=SQLiteStore(db_path), redis_url="redis://example/0")
    second_bus.emit("worker.completed", source="runtime")
    restarted = EventBus(store=SQLiteStore(db_path), redis_url="redis://example/0")
    restarted.emit("job.completed", source="scheduler")

    assert [entry[0] for entry in redis.entries] == [
        "1700000000000-0",
        "1700000000001-0",
        "1700000000002-0",
    ]
    assert [json.loads(entry[1]["event"]).get("sequence") for entry in redis.entries] == [
        None,
        2,
        3,
    ]


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

    monkeypatch.setattr("goblin_king.events._redis_client", lambda _url: FakeRedis())

    status = stream_status("redis://example/0")

    assert status["ok"] is True
    assert status["length"] == 4
    assert status["pending"] == 3
    assert status["last_generated_id"] == "4-0"
    assert status["groups"][0]["name"] == "workers"
