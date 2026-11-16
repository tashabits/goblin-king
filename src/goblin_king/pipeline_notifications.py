"""Durable admin events and bounded authenticated HTTP notification delivery."""

import json
import time
from datetime import datetime
from urllib.parse import urlsplit
from urllib.request import Request, build_opener

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from goblin_king.contracts import EventRecord
from goblin_king.pipeline_oci import NoRedirect
from goblin_king.store_schema import events_table
from goblin_king.store_schema import pipeline_notifications_table as notifications


def deliver_notifications(state, settings):
    with state.store.engine.connect() as connection:
        rows = (
            connection.execute(
                select(notifications)
                .where(
                    notifications.c.status == "pending", notifications.c.not_before <= time.time()
                )
                .limit(100)
            )
            .mappings()
            .all()
        )
    for row in rows:
        body = json.loads(row["document"])
        with state.store.engine.connect() as connection:
            existing = connection.execute(
                select(events_table.c.id).where(events_table.c.id == row["id"])
            ).scalar_one_or_none()
        if existing is None:
            try:
                state.store.persist_event(
                    EventRecord(
                        id=row["id"],
                        source="api",
                        event_type="pipeline." + body["action"],
                        project_id=body["project_id"],
                        created_at=datetime.fromisoformat(body["at"]),
                        payload=body,
                    )
                )
            except IntegrityError:
                with state.store.engine.connect() as connection:
                    if (
                        connection.execute(
                            select(events_table.c.id).where(events_table.c.id == row["id"])
                        ).scalar_one_or_none()
                        is None
                    ):
                        raise
        try:
            if settings.notification_url:
                url = urlsplit(settings.notification_url)
                if url.username or url.password or url.fragment or url.scheme != "https":
                    raise ValueError(
                        "notification sink requires HTTPS without embedded credentials"
                    )
                token = settings.notification_token_path.read_text().strip()
                if not token:
                    raise ValueError("notification authentication Secret is empty")
                request = Request(
                    settings.notification_url,
                    data=json.dumps(body).encode(),
                    headers={
                        "Authorization": "Bearer " + token,
                        "Content-Type": "application/json",
                        "Idempotency-Key": row["id"],
                    },
                )
                with build_opener(NoRedirect()).open(request, timeout=10) as response:
                    if not 200 <= response.status < 300:
                        raise ValueError("notification not acknowledged")
        except Exception:
            attempts = row["attempts"] + 1
            values = {
                "attempts": attempts,
                "not_before": time.time() + min(300, 2**attempts),
                "status": "held" if attempts >= 8 else "pending",
            }
        else:
            values = {"status": "delivered"}
        with state.transaction() as connection:
            connection.execute(
                notifications.update()
                .where(
                    notifications.c.id == row["id"],
                    notifications.c.status == "pending",
                    notifications.c.attempts == row["attempts"],
                )
                .values(**values)
            )
