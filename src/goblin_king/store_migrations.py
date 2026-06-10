"""SQLite schema compatibility helpers for existing Goblin King databases."""

from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine


def ensure_schema_columns(engine: Engine) -> None:
    """Add compatibility columns to existing SQLite databases."""
    job_columns = {column["name"] for column in inspect(engine).get_columns("jobs")}
    job_additions = {
        "fanout_id": "TEXT",
        "project_id": "TEXT",
        "metadata_json": "TEXT NOT NULL DEFAULT '{}'",
        "status": "TEXT NOT NULL DEFAULT 'queued'",
        "priority": "INTEGER NOT NULL DEFAULT 100",
        "schedule_id": "TEXT",
        "due_at": "DATETIME",
        "lease_owner": "TEXT",
        "leased_until": "DATETIME",
        "attempt_count": "INTEGER NOT NULL DEFAULT 0",
        "max_retries": "INTEGER NOT NULL DEFAULT 0",
        "timeout_seconds": "INTEGER",
        "last_error": "TEXT",
    }
    run_columns = {column["name"] for column in inspect(engine).get_columns("runs")}
    run_additions = {
        "project_id": "TEXT",
        "timeout_seconds": "INTEGER",
        "max_retries": "INTEGER NOT NULL DEFAULT 0",
        "leased_until": "DATETIME",
        "resource_policy_json": "TEXT",
    }
    fanout_columns = {column["name"] for column in inspect(engine).get_columns("fanouts")}
    schedule_columns = {
        column["name"] for column in inspect(engine).get_columns("schedules")
    }
    event_columns = {column["name"] for column in inspect(engine).get_columns("events")}
    with engine.begin() as connection:
        for column_name, ddl in job_additions.items():
            if column_name not in job_columns:
                connection.execute(text(f"ALTER TABLE jobs ADD COLUMN {column_name} {ddl}"))
        for column_name, ddl in run_additions.items():
            if column_name not in run_columns:
                connection.execute(text(f"ALTER TABLE runs ADD COLUMN {column_name} {ddl}"))
        if "project_id" not in fanout_columns:
            connection.execute(text("ALTER TABLE fanouts ADD COLUMN project_id TEXT"))
        if "project_id" not in schedule_columns:
            connection.execute(text("ALTER TABLE schedules ADD COLUMN project_id TEXT"))
        if "project_id" not in event_columns:
            connection.execute(text("ALTER TABLE events ADD COLUMN project_id TEXT"))
