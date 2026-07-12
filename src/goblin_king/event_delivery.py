"""Ordered, replay-safe delivery of durable events to one Redis Stream."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from redis.exceptions import ResponseError
from sqlalchemy import select, update
from sqlalchemy.engine import Engine

from goblin_king.store_rows import _row_to_event
from goblin_king.store_schema import event_stream_deliveries_table, events_table

MAX_DELIVERY_BATCH = 100


class OrderedEventStreamDelivery:
    """Serialize one SQLite event sequence into a Redis Stream."""

    def __init__(
        self,
        *,
        engine: Engine,
        redis: Any,
        redis_url: str,
        stream: str,
        maxlen: int,
    ) -> None:
        self.engine = engine
        self.redis = redis
        self.stream = stream
        self.maxlen = maxlen
        target = f"{redis_url}\0{stream}".encode()
        self.target = hashlib.sha256(target).hexdigest()

    def deliver_through(self, target_sequence: int) -> None:
        """Append available events in order through a bounded target sequence."""
        with self.engine.connect() as connection:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    event_stream_deliveries_table.insert()
                    .prefix_with("OR IGNORE")
                    .values(
                        target=self.target,
                        delivered_sequence=0,
                        stream_id_offset=None,
                    )
                )
                state = connection.execute(
                    select(event_stream_deliveries_table).where(
                        event_stream_deliveries_table.c.target == self.target
                    )
                ).mappings().one()
                cursor = int(state["delivered_sequence"])
                offset = state["stream_id_offset"]
                if offset is None:
                    cursor, offset = self._bootstrap(connection, cursor)
                    self._save_state(connection, cursor, offset)
                while cursor < target_sequence:
                    rows = (
                        connection.execute(
                            select(events_table)
                            .where(events_table.c.sequence > cursor)
                            .where(events_table.c.sequence <= target_sequence)
                            .order_by(events_table.c.sequence)
                            .limit(MAX_DELIVERY_BATCH)
                        )
                        .mappings()
                        .all()
                    )
                    if not rows:
                        cursor = target_sequence
                        self._save_state(connection, cursor, int(offset))
                        break
                    for row in rows:
                        sequence = int(row["sequence"])
                        if sequence > cursor + 1:
                            cursor = sequence - 1
                            self._save_state(connection, cursor, int(offset))
                        self._append(int(offset), _event_payload(row))
                        cursor = sequence
                        self._save_state(connection, cursor, int(offset))
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def _bootstrap(self, connection: Any, cursor: int) -> tuple[int, int]:
        entries = self.redis.xrevrange(self.stream, count=1)
        if not entries:
            first = connection.execute(
                select(events_table.c.sequence).order_by(events_table.c.sequence).limit(1)
            ).scalar_one_or_none()
            return (max(cursor, int(first) - 1) if first else cursor, 0)
        message_id, fields = entries[0]
        last_milliseconds = int(_text(message_id).split("-", 1)[0])
        payload = _stream_payload(fields)
        sequence = int(payload.get("sequence") or 0)
        if sequence < 1 and payload.get("id"):
            sequence = int(
                connection.execute(
                    select(events_table.c.sequence).where(
                        events_table.c.id == str(payload["id"])
                    )
                ).scalar_one_or_none()
                or 0
            )
        if sequence < 1:
            return cursor, last_milliseconds
        return max(cursor, sequence), max(0, last_milliseconds - sequence)

    def _append(self, offset: int, payload: dict[str, Any]) -> None:
        stream_id = f"{offset + int(payload['sequence'])}-0"
        serialized = json.dumps(payload)
        try:
            self.redis.xadd(
                self.stream,
                {"event": serialized},
                id=stream_id,
                maxlen=self.maxlen,
                approximate=True,
            )
        except ResponseError:
            entries = self.redis.xrange(self.stream, min=stream_id, max=stream_id, count=1)
            if not entries or _stream_payload(entries[0][1]) != payload:
                raise

    def _save_state(self, connection: Any, sequence: int, offset: int) -> None:
        connection.execute(
            update(event_stream_deliveries_table)
            .where(event_stream_deliveries_table.c.target == self.target)
            .values(delivered_sequence=sequence, stream_id_offset=offset)
        )


def _event_payload(row: Any) -> dict[str, Any]:
    return _row_to_event(dict(row)).model_dump(mode="json")


def _stream_payload(fields: dict[Any, Any]) -> dict[str, Any]:
    raw = fields.get(b"event") or fields.get("event")
    return json.loads(_text(raw)) if raw else {}


def _text(value: Any) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)
