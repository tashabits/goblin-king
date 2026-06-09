"""Event and heartbeat publishing helpers for durable and live status updates."""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from redis import Redis
from redis.exceptions import RedisError

from goblin_king.contracts import EventRecord, HeartbeatRecord, utc_now
from goblin_king.store import SQLiteStore

DEFAULT_EVENT_CHANNEL = "goblin-king:events"
DEFAULT_HEARTBEAT_CHANNEL = "goblin-king:heartbeats"
DEFAULT_HEARTBEAT_INTERVAL_SECONDS = 5


class EventBus:
    """Persist events to SQLite and mirror them onto Redis pub/sub channels."""

    def __init__(
        self,
        *,
        store: SQLiteStore,
        redis_url: str = "redis://localhost:6379/0",
        event_channel: str = DEFAULT_EVENT_CHANNEL,
        heartbeat_channel: str = DEFAULT_HEARTBEAT_CHANNEL,
    ) -> None:
        self.store = store
        self.redis_url = redis_url
        self.event_channel = event_channel
        self.heartbeat_channel = heartbeat_channel

    def emit(
        self,
        event_type: str,
        *,
        source: str,
        payload: dict[str, Any] | None = None,
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


def worker_heartbeat_key(run_id: str) -> str:
    """Return the Redis list key used by Docker workers for heartbeat handoff."""
    return f"goblin-king:heartbeats:{run_id}"
