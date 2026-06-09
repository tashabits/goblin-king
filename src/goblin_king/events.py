"""Event and heartbeat publishing helpers for durable and live status updates."""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from redis import Redis
from redis.exceptions import RedisError, ResponseError

from goblin_king.contracts import EventRecord, HeartbeatRecord, utc_now
from goblin_king.store import SQLiteStore

DEFAULT_EVENT_CHANNEL = "goblin-king:events"
DEFAULT_HEARTBEAT_CHANNEL = "goblin-king:heartbeats"
DEFAULT_EVENT_STREAM = "goblin-king:events:stream"
DEFAULT_EVENT_STREAM_GROUP = "goblin-king-event-readers"
DEFAULT_EVENT_STREAM_MAXLEN = 10_000
DEFAULT_HEARTBEAT_INTERVAL_SECONDS = 5


class EventBus:
    """Persist events to SQLite and mirror them onto Redis pub/sub and streams."""

    def __init__(
        self,
        *,
        store: SQLiteStore,
        redis_url: str = "redis://localhost:6379/0",
        event_channel: str = DEFAULT_EVENT_CHANNEL,
        heartbeat_channel: str = DEFAULT_HEARTBEAT_CHANNEL,
        event_stream: str = DEFAULT_EVENT_STREAM,
        event_stream_maxlen: int = DEFAULT_EVENT_STREAM_MAXLEN,
    ) -> None:
        self.store = store
        self.redis_url = redis_url
        self.event_channel = event_channel
        self.heartbeat_channel = heartbeat_channel
        self.event_stream = event_stream
        self.event_stream_maxlen = event_stream_maxlen

    def emit(
        self,
        event_type: str,
        *,
        source: str,
        payload: dict[str, Any] | None = None,
        project_id: str | None = None,
        job_id: str | None = None,
        run_id: str | None = None,
        fanout_id: str | None = None,
        schedule_id: str | None = None,
        worker_id: str | None = None,
        scheduler_id: str | None = None,
    ) -> EventRecord:
        """Persist and publish one event envelope."""
        event = EventRecord(
            id=str(uuid4()),
            created_at=utc_now(),
            event_type=event_type,
            source=source,
            project_id=project_id,
            job_id=job_id,
            run_id=run_id,
            fanout_id=fanout_id,
            schedule_id=schedule_id,
            worker_id=worker_id,
            scheduler_id=scheduler_id,
            payload=payload or {},
        )
        self.store.save_event(event)
        self._publish(self.event_channel, event.model_dump(mode="json"))
        self._append_stream(event.model_dump(mode="json"))
        return event

    def heartbeat(
        self,
        *,
        owner_id: str,
        owner_type: str,
        status: str,
        payload: dict[str, Any] | None = None,
        job_id: str | None = None,
        run_id: str | None = None,
    ) -> HeartbeatRecord:
        """Persist and publish the latest heartbeat for one owner."""
        heartbeat = HeartbeatRecord(
            owner_id=owner_id,
            owner_type=owner_type,
            status=status,
            last_seen_at=utc_now(),
            job_id=job_id,
            run_id=run_id,
            payload=payload or {},
        )
        self.store.upsert_heartbeat(heartbeat)
        self._publish(self.heartbeat_channel, heartbeat.model_dump(mode="json"))
        return heartbeat

    def record_worker_heartbeat_payload(self, raw_payload: str | bytes) -> HeartbeatRecord | None:
        """Persist one worker heartbeat payload, recording malformed data as an event."""
        try:
            text = raw_payload.decode("utf-8") if isinstance(raw_payload, bytes) else raw_payload
            payload = json.loads(text)
            heartbeat = HeartbeatRecord.model_validate(payload)
        except (TypeError, ValueError) as error:
            self.emit(
                "worker.heartbeat_invalid",
                source="runtime",
                payload={"error": str(error), "raw": str(raw_payload)},
            )
            return None
        self.store.upsert_heartbeat(heartbeat)
        self._publish(self.heartbeat_channel, heartbeat.model_dump(mode="json"))
        return heartbeat

    def _publish(self, channel: str, payload: dict[str, Any]) -> None:
        """Publish JSON onto Redis when available; durability already lives in SQLite."""
        try:
            Redis.from_url(self.redis_url).publish(channel, json.dumps(payload))
        except RedisError:
            return

    def _append_stream(self, payload: dict[str, Any]) -> None:
        """Append an event envelope to Redis Streams when available."""
        try:
            Redis.from_url(self.redis_url).xadd(
                self.event_stream,
                {"event": json.dumps(payload)},
                maxlen=self.event_stream_maxlen,
                approximate=True,
            )
        except RedisError:
            return


def stream_status(
    redis_url: str,
    *,
    stream: str = DEFAULT_EVENT_STREAM,
) -> dict[str, Any]:
    """Return Redis Stream health details without mutating stream state."""
    try:
        client = Redis.from_url(redis_url)
        info = client.xinfo_stream(stream)
        groups = client.xinfo_groups(stream)
        decoded_groups = [_decode_redis_mapping(group) for group in groups]
        pending = sum(int(group.get("pending", 0)) for group in decoded_groups)
        return {
            "stream": stream,
            "ok": True,
            "length": int(info.get("length", 0)),
            "last_generated_id": _decode_redis_value(info.get("last-generated-id")),
            "groups": decoded_groups,
            "pending": pending,
            "error": None,
        }
    except ResponseError as error:
        return {
            "stream": stream,
            "ok": False,
            "length": 0,
            "last_generated_id": None,
            "groups": [],
            "pending": 0,
            "error": str(error),
        }
    except RedisError as error:
        return {
            "stream": stream,
            "ok": False,
            "length": 0,
            "last_generated_id": None,
            "groups": [],
            "pending": 0,
            "error": str(error),
        }


def ensure_stream_group(
    redis_url: str,
    *,
    stream: str = DEFAULT_EVENT_STREAM,
    group: str = DEFAULT_EVENT_STREAM_GROUP,
) -> None:
    """Create the default consumer group if it does not already exist."""
    try:
        Redis.from_url(redis_url).xgroup_create(stream, group, id="0", mkstream=True)
    except ResponseError as error:
        if "BUSYGROUP" not in str(error):
            raise


def read_stream_group(
    redis_url: str,
    *,
    stream: str = DEFAULT_EVENT_STREAM,
    group: str = DEFAULT_EVENT_STREAM_GROUP,
    consumer: str = "goblin-king-cli",
    count: int = 10,
    ack: bool = False,
) -> list[dict[str, Any]]:
    """Read event envelopes through a Redis Stream consumer group."""
    ensure_stream_group(redis_url, stream=stream, group=group)
    client = Redis.from_url(redis_url)
    messages = client.xreadgroup(group, consumer, {stream: ">"}, count=count)
    events: list[dict[str, Any]] = []
    ids_to_ack: list[str] = []
    for _stream_name, stream_messages in messages:
        for message_id, fields in stream_messages:
            decoded_id = _decode_redis_value(message_id)
            event_raw = fields.get(b"event") or fields.get("event")
            event_text = _decode_redis_value(event_raw)
            event = json.loads(event_text) if event_text else {}
            event["_stream_id"] = decoded_id
            events.append(event)
            ids_to_ack.append(decoded_id)
    if ack and ids_to_ack:
        client.xack(stream, group, *ids_to_ack)
    return events


def _decode_redis_mapping(mapping: dict[Any, Any]) -> dict[str, Any]:
    """Decode Redis byte keys/values in stream metadata responses."""
    return {
        str(_decode_redis_value(key)): _decode_redis_value(value)
        for key, value in mapping.items()
    }


def _decode_redis_value(value: Any) -> Any:
    """Decode one Redis response value while preserving numbers and nulls."""
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return value


def worker_heartbeat_key(run_id: str) -> str:
    """Return the Redis list key used by Docker workers for heartbeat handoff."""
    return f"goblin-king:heartbeats:{run_id}"
