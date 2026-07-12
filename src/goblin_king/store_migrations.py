"""SQLite schema compatibility helpers for existing Goblin King databases."""

from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine


def ensure_schema_columns(engine: Engine) -> None:
    """Add compatibility columns to existing SQLite databases."""
    inspector = inspect(engine)
    job_columns = {column["name"] for column in inspector.get_columns("jobs")}
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
    run_columns = {column["name"] for column in inspector.get_columns("runs")}
    run_additions = {
        "project_id": "TEXT",
        "timeout_seconds": "INTEGER",
        "max_retries": "INTEGER NOT NULL DEFAULT 0",
        "leased_until": "DATETIME",
        "resource_policy_json": "TEXT",
    }
    fanout_columns = {column["name"] for column in inspector.get_columns("fanouts")}
    schedule_columns = {
        column["name"] for column in inspector.get_columns("schedules")
    }
    event_columns = {column["name"] for column in inspector.get_columns("events")}
    long_service_columns = {
        column["name"] for column in inspector.get_columns("long_services")
    }
    repository_entry_columns = {
        column["name"] for column in inspector.get_columns("repository_entries")
    }
    repository_entry_additions = {
        "name": "TEXT NOT NULL DEFAULT ''",
        "kind": "TEXT NOT NULL DEFAULT ''",
        "type": "TEXT NOT NULL DEFAULT 'notebook_function'",
        "project_id": "TEXT",
        "owner": "TEXT NOT NULL DEFAULT ''",
        "display_name": "TEXT NOT NULL DEFAULT ''",
        "description": "TEXT",
        "tags_json": "TEXT NOT NULL DEFAULT '[]'",
        "status": "TEXT NOT NULL DEFAULT 'draft'",
        "published_version": "INTEGER",
        "created_at": "DATETIME",
        "updated_at": "DATETIME",
    }
    repository_version_columns = {
        column["name"] for column in inspector.get_columns("repository_versions")
    }
    repository_version_additions = {
        "entry_id": "TEXT NOT NULL DEFAULT ''",
        "version": "INTEGER NOT NULL DEFAULT 1",
        "kind": "TEXT NOT NULL DEFAULT 'repository.unknown.v1'",
        "source_hash": "TEXT NOT NULL DEFAULT ''",
        "runner_image": "TEXT NOT NULL DEFAULT ''",
        "validation_proof_json": "TEXT NOT NULL DEFAULT '{}'",
        "approval_status": "TEXT NOT NULL DEFAULT 'draft'",
        "status": "TEXT NOT NULL DEFAULT 'draft'",
        "approved_by": "TEXT",
        "approved_at": "DATETIME",
        "published_at": "DATETIME",
        "created_at": "DATETIME",
        "updated_at": "DATETIME",
    }
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
        if "sequence" not in event_columns:
            connection.execute(
                text("ALTER TABLE events ADD COLUMN sequence INTEGER NOT NULL DEFAULT 0")
            )
        if connection.execute(text("SELECT COUNT(*) FROM events WHERE sequence = 0")).scalar_one():
            connection.execute(
                text(
                    "WITH ordered AS ("
                    "SELECT id, ROW_NUMBER() OVER (ORDER BY rowid) AS causal_sequence "
                    "FROM events"
                    ") "
                    "UPDATE events SET sequence = ("
                    "SELECT causal_sequence FROM ordered WHERE ordered.id = events.id"
                    ")"
                )
            )
        connection.execute(
            text(
                "INSERT OR IGNORE INTO causal_sequences (scope, value) "
                "SELECT 'events', COALESCE(MAX(sequence), 0) FROM events"
            )
        )
        connection.execute(
            text(
                "UPDATE causal_sequences SET value = MAX("
                "value, (SELECT COALESCE(MAX(sequence), 0) FROM events)"
                ") WHERE scope = 'events'"
            )
        )
        if "probe_path" not in long_service_columns:
            connection.execute(
                text(
                    "ALTER TABLE long_services "
                    "ADD COLUMN probe_path TEXT NOT NULL DEFAULT '/hello'"
                )
            )
        for column_name, ddl in repository_entry_additions.items():
            if column_name not in repository_entry_columns:
                connection.execute(
                    text(f"ALTER TABLE repository_entries ADD COLUMN {column_name} {ddl}")
                )
        for column_name, ddl in repository_version_additions.items():
            if column_name not in repository_version_columns:
                connection.execute(
                    text(f"ALTER TABLE repository_versions ADD COLUMN {column_name} {ddl}")
                )
        connection.execute(
            text(
                "UPDATE runs SET finished_at = started_at "
                "WHERE finished_at IS NOT NULL AND finished_at < started_at"
            )
        )
        connection.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS "
                "uq_repository_entries_active_project_name "
                "ON repository_entries (COALESCE(project_id, ''), name) "
                "WHERE status != 'retired'"
            )
        )
        connection.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS "
                "ix_repository_versions_entry_version "
                "ON repository_versions (entry_id, version)"
            )
        )
        connection.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS ix_events_causal_sequence "
                "ON events (sequence)"
            )
        )
