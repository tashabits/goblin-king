"""Bounded, replayable worker run events for progress and live text output."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from redis import Redis
from redis.exceptions import RedisError

from goblin_king.contracts import RunEventEnvelope, RunEventType, utc_now
from goblin_king.versions import WORKER_RUN_EVENT_CONTRACT_VERSION

DEFAULT_RUN_EVENT_MAX_EVENTS = 256
DEFAULT_RUN_EVENT_MAX_PAYLOAD_BYTES = 4 * 1024
DEFAULT_RUN_EVENT_MIN_INTERVAL_MS = 50
DEFAULT_RUN_EVENT_TTL_SECONDS = 60 * 60
DEFAULT_RUN_EVENT_READ_LIMIT = 100
MAX_RUN_EVENT_READ_LIMIT = 256
RUN_EVENT_TYPES = frozenset({"progress", "stdout", "stderr", "message"})

RUN_EVENT_REDIS_URL_ENV = "GOBLIN_RUN_EVENT_REDIS_URL"
RUN_EVENT_STREAM_ENV = "GOBLIN_RUN_EVENT_STREAM"
RUN_EVENT_SEQUENCE_KEY_ENV = "GOBLIN_RUN_EVENT_SEQUENCE_KEY"
RUN_EVENT_RATE_KEY_ENV = "GOBLIN_RUN_EVENT_RATE_KEY"
RUN_EVENT_MAX_EVENTS_ENV = "GOBLIN_RUN_EVENT_MAX_EVENTS"
RUN_EVENT_MAX_PAYLOAD_BYTES_ENV = "GOBLIN_RUN_EVENT_MAX_PAYLOAD_BYTES"
RUN_EVENT_MIN_INTERVAL_MS_ENV = "GOBLIN_RUN_EVENT_MIN_INTERVAL_MS"
RUN_EVENT_TTL_SECONDS_ENV = "GOBLIN_RUN_EVENT_TTL_SECONDS"
RUN_EVENT_CONTRACT_VERSION_ENV = "GOBLIN_RUN_EVENT_CONTRACT_VERSION"


class RunEventError(ValueError):
    """Raised when a run event violates its bounded worker contract."""


class RunEventTransportError(RuntimeError):
    """Raised when the replayable Redis transport cannot be read or written."""


def run_event_stream_key(run_id: str) -> str:
    """Return the exact run-local Redis Stream key."""
    return f"goblin-king:run-events:{run_id}"


def run_event_sequence_key(run_id: str) -> str:
    """Return the exact run-local monotonic sequence key."""
    return f"goblin-king:run-events:{run_id}:sequence"


def run_event_rate_key(run_id: str) -> str:
    """Return the exact run-local publisher rate key."""
    return f"goblin-king:run-events:{run_id}:rate"


def worker_run_event_environment(run_id: str, redis_url: str) -> dict[str, str]:
    """Build the additive fixed-worker environment shared by both runtimes."""
    return {
        RUN_EVENT_CONTRACT_VERSION_ENV: WORKER_RUN_EVENT_CONTRACT_VERSION,
        RUN_EVENT_REDIS_URL_ENV: redis_url,
        RUN_EVENT_STREAM_ENV: run_event_stream_key(run_id),
        RUN_EVENT_SEQUENCE_KEY_ENV: run_event_sequence_key(run_id),
        RUN_EVENT_RATE_KEY_ENV: run_event_rate_key(run_id),
        RUN_EVENT_MAX_EVENTS_ENV: str(DEFAULT_RUN_EVENT_MAX_EVENTS),
        RUN_EVENT_MAX_PAYLOAD_BYTES_ENV: str(DEFAULT_RUN_EVENT_MAX_PAYLOAD_BYTES),
        RUN_EVENT_MIN_INTERVAL_MS_ENV: str(DEFAULT_RUN_EVENT_MIN_INTERVAL_MS),
        RUN_EVENT_TTL_SECONDS_ENV: str(DEFAULT_RUN_EVENT_TTL_SECONDS),
    }


class RunEventPublisher:
    """Publish bounded worker events without receiving any control-plane credential."""

    def __init__(
        self,
        *,
        redis: Any,
        run_id: str,
        stream: str,
        sequence_key: str,
        rate_key: str,
        max_events: int = DEFAULT_RUN_EVENT_MAX_EVENTS,
        max_payload_bytes: int = DEFAULT_RUN_EVENT_MAX_PAYLOAD_BYTES,
        min_interval_ms: int = DEFAULT_RUN_EVENT_MIN_INTERVAL_MS,
        ttl_seconds: int = DEFAULT_RUN_EVENT_TTL_SECONDS,
    ) -> None:
        if not 1 <= max_events <= DEFAULT_RUN_EVENT_MAX_EVENTS:
            raise RunEventError(f"max_events must be between 1 and {DEFAULT_RUN_EVENT_MAX_EVENTS}")
        if not 1 <= max_payload_bytes <= DEFAULT_RUN_EVENT_MAX_PAYLOAD_BYTES:
            raise RunEventError(
                f"max_payload_bytes must be between 1 and {DEFAULT_RUN_EVENT_MAX_PAYLOAD_BYTES}"
            )
        if not DEFAULT_RUN_EVENT_MIN_INTERVAL_MS <= min_interval_ms <= 60_000:
            raise RunEventError(
                f"min_interval_ms must be at least {DEFAULT_RUN_EVENT_MIN_INTERVAL_MS}"
            )
        if not 1 <= ttl_seconds <= DEFAULT_RUN_EVENT_TTL_SECONDS:
            raise RunEventError(
                f"ttl_seconds must be between 1 and {DEFAULT_RUN_EVENT_TTL_SECONDS}"
            )
        self.redis = redis
        self.run_id = run_id
        self.stream = stream
        self.sequence_key = sequence_key
        self.rate_key = rate_key
        self.max_events = max_events
        self.max_payload_bytes = max_payload_bytes
        self.min_interval_ms = min_interval_ms
        self.ttl_seconds = ttl_seconds

    @classmethod
    def from_environment(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        redis_factory: Any | None = None,
    ) -> RunEventPublisher:
        """Build a publisher from the narrow additive worker environment."""
        values = os.environ if environ is None else environ
        version = values[RUN_EVENT_CONTRACT_VERSION_ENV]
        if version != WORKER_RUN_EVENT_CONTRACT_VERSION:
            raise RunEventError(f"unsupported run event contract: {version}")
        run_id = values["GOBLIN_RUN_ID"]
        stream = values[RUN_EVENT_STREAM_ENV]
        sequence_key = values[RUN_EVENT_SEQUENCE_KEY_ENV]
        rate_key = values[RUN_EVENT_RATE_KEY_ENV]
        expected = worker_run_event_environment(run_id, values[RUN_EVENT_REDIS_URL_ENV])
        for key, actual in {
            RUN_EVENT_STREAM_ENV: stream,
            RUN_EVENT_SEQUENCE_KEY_ENV: sequence_key,
            RUN_EVENT_RATE_KEY_ENV: rate_key,
        }.items():
            if actual != expected[key]:
                raise RunEventError(f"{key} does not match GOBLIN_RUN_ID")
        factory = redis_factory or Redis.from_url
        return cls(
            redis=factory(values[RUN_EVENT_REDIS_URL_ENV]),
            run_id=run_id,
            stream=stream,
            sequence_key=sequence_key,
            rate_key=rate_key,
            max_events=int(values[RUN_EVENT_MAX_EVENTS_ENV]),
            max_payload_bytes=int(values[RUN_EVENT_MAX_PAYLOAD_BYTES_ENV]),
            min_interval_ms=int(values[RUN_EVENT_MIN_INTERVAL_MS_ENV]),
            ttl_seconds=int(values[RUN_EVENT_TTL_SECONDS_ENV]),
        )

    def emit(
        self,
        event_type: RunEventType,
        payload: dict[str, Any] | None = None,
        *,
        created_at: datetime | None = None,
    ) -> RunEventEnvelope:
        """Append one exactly bounded event and return its monotonic envelope."""
        payload = payload or {}
        if event_type not in RUN_EVENT_TYPES:
            raise RunEventError(f"unsupported run event type: {event_type}")
        try:
            payload_json = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        except (TypeError, ValueError) as error:
            raise RunEventError("run event payload must be JSON serializable") from error
        payload_bytes = len(payload_json.encode("utf-8"))
        if payload_bytes > self.max_payload_bytes:
            raise RunEventError(
                f"run event payload exceeds {self.max_payload_bytes} bytes: {payload_bytes}"
            )
        try:
            accepted = self.redis.set(
                self.rate_key,
                "1",
                nx=True,
                px=self.min_interval_ms,
            )
            if not accepted:
                raise RunEventError(f"run events are limited to one every {self.min_interval_ms}ms")
            sequence = int(self.redis.incr(self.sequence_key))
            event = RunEventEnvelope(
                sequence=sequence,
                created_at=created_at or utc_now(),
                event_type=event_type,
                run_id=self.run_id,
                payload=payload,
            )
            pipe = self.redis.pipeline(transaction=True)
            pipe.xadd(
                self.stream,
                {"event": event.model_dump_json()},
                maxlen=self.max_events,
                approximate=False,
            )
            pipe.expire(self.stream, self.ttl_seconds)
            pipe.expire(self.sequence_key, self.ttl_seconds)
            pipe.execute()
            return event
        except RunEventError:
            raise
        except (RedisError, TypeError, ValueError) as error:
            raise RunEventTransportError("run event transport is unavailable") from error

    def try_emit(
        self,
        event_type: RunEventType,
        payload: dict[str, Any] | None = None,
    ) -> RunEventEnvelope | None:
        """Publish best-effort progress without changing the worker's task outcome."""
        try:
            return self.emit(event_type, payload)
        except (RunEventError, RunEventTransportError):
            return None

    def progress(self, percent: int | float, message: str | None = None) -> RunEventEnvelope:
        """Publish a conventional progress payload."""
        if not 0 <= percent <= 100:
            raise RunEventError("progress percent must be between 0 and 100")
        payload: dict[str, Any] = {"percent": percent}
        if message is not None:
            payload["message"] = message
        return self.emit("progress", payload)

    def stdout(self, text: str) -> RunEventEnvelope:
        """Publish one bounded stdout text chunk."""
        return self.emit("stdout", {"text": text})

    def stderr(self, text: str) -> RunEventEnvelope:
        """Publish one bounded stderr text chunk."""
        return self.emit("stderr", {"text": text})


def read_run_event_entries(
    redis_url: str,
    run_id: str,
    *,
    after_sequence: int = 0,
    limit: int = DEFAULT_RUN_EVENT_READ_LIMIT,
    redis_factory: Any | None = None,
) -> list[tuple[str, RunEventEnvelope]]:
    """Read retained validated events for exactly one run in monotonic order."""
    if after_sequence < 0:
        raise RunEventError("after_sequence must be non-negative")
    if not 1 <= limit <= MAX_RUN_EVENT_READ_LIMIT:
        raise RunEventError(f"limit must be between 1 and {MAX_RUN_EVENT_READ_LIMIT}")
    try:
        factory = redis_factory or Redis.from_url
        entries = factory(redis_url).xrange(
            run_event_stream_key(run_id),
            min="-",
            max="+",
            count=DEFAULT_RUN_EVENT_MAX_EVENTS,
        )
    except RedisError as error:
        raise RunEventTransportError("run event transport is unavailable") from error
    decoded: list[tuple[str, RunEventEnvelope]] = []
    for stream_id, fields in entries:
        raw = fields.get(b"event") or fields.get("event")
        if raw is None:
            continue
        text = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
        if len(text.encode("utf-8")) > DEFAULT_RUN_EVENT_MAX_PAYLOAD_BYTES + 1024:
            continue
        try:
            event = RunEventEnvelope.model_validate_json(text)
        except ValueError:
            continue
        if (
            len(
                json.dumps(event.payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            )
            > DEFAULT_RUN_EVENT_MAX_PAYLOAD_BYTES
        ):
            continue
        if event.run_id != run_id or event.sequence <= after_sequence:
            continue
        decoded_id = stream_id.decode("utf-8") if isinstance(stream_id, bytes) else str(stream_id)
        decoded.append((decoded_id, event))
    decoded.sort(key=lambda item: item[1].sequence)
    monotonic: list[tuple[str, RunEventEnvelope]] = []
    last_sequence = after_sequence
    for item in decoded:
        if item[1].sequence <= last_sequence:
            continue
        monotonic.append(item)
        last_sequence = item[1].sequence
    return monotonic[:limit]
