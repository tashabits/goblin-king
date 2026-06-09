"""SQLite persistence for Phase 1 job and run records."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    inspect,
    or_,
    select,
    text,
    update,
)
from sqlalchemy.engine import Engine

from goblin_king.contracts import (
    ArtifactRecord,
    GoblinResult,
    HandoffRecord,
    JobRecord,
    RunRecord,
    ScheduleRecord,
)

DEFAULT_DB_PATH = Path(".goblin-king") / "goblin-king.sqlite3"

metadata = MetaData()

jobs_table = Table(
    "jobs",
    metadata,
    # Column definitions are intentionally explicit so the Phase 1 schema is easy to inspect.
    Column("id", String, primary_key=True),
    Column("kind", String, nullable=False),
    Column("input_json", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("created_by", String, nullable=False),
    Column("correlation_id", String, nullable=True),
    Column("status", String, nullable=False, default="queued"),
    Column("priority", Integer, nullable=False, default=100),
    Column("schedule_id", String, nullable=True),
    Column("due_at", DateTime(timezone=True), nullable=True),
    Column("lease_owner", String, nullable=True),
    Column("leased_until", DateTime(timezone=True), nullable=True),
    Column("attempt_count", Integer, nullable=False, default=0),
    Column("max_retries", Integer, nullable=False, default=0),
    Column("timeout_seconds", Integer, nullable=True),
    Column("last_error", Text, nullable=True),
)

schedules_table = Table(
    "schedules",
    metadata,
    Column("id", String, primary_key=True),
    Column("kind", String, nullable=False),
    Column("input_json", Text, nullable=False),
    Column("cron", String, nullable=False),
    Column("timezone", String, nullable=False),
    Column("enabled", Integer, nullable=False, default=1),
    Column("priority", Integer, nullable=False, default=100),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("next_run_at", DateTime(timezone=True), nullable=False),
    Column("last_materialized_at", DateTime(timezone=True), nullable=True),
    Column("max_retries", Integer, nullable=False, default=0),
    Column("timeout_seconds", Integer, nullable=True),
)

runs_table = Table(
    "runs",
    metadata,
    Column("id", String, primary_key=True),
    Column("job_id", String, ForeignKey("jobs.id"), nullable=False),
    Column("kind", String, nullable=False),
    Column("attempt", Integer, nullable=False),
    Column("status", String, nullable=False),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("finished_at", DateTime(timezone=True), nullable=True),
    Column("result_json", Text, nullable=True),
    Column("error", Text, nullable=True),
    Column("timeout_seconds", Integer, nullable=True),
    Column("max_retries", Integer, nullable=False, default=0),
    Column("leased_until", DateTime(timezone=True), nullable=True),
)

artifacts_table = Table(
    "artifacts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("run_id", String, ForeignKey("runs.id"), nullable=False),
    Column("name", String, nullable=False),
    Column("uri", Text, nullable=False),
    Column("media_type", String, nullable=True),
)

handoffs_table = Table(
    "handoffs",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("run_id", String, ForeignKey("runs.id"), nullable=False),
    Column("kind", String, nullable=False),
    Column("payload_json", Text, nullable=False),
)


class SQLiteStore:
    """Persist Phase 1 jobs, runs, artifacts, and handoffs in a local SQLite database."""

    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.engine: Engine = create_engine(f"sqlite:///{self.db_path}")
        metadata.create_all(self.engine)
        self._ensure_phase2_columns()

    def save_job(self, job: JobRecord) -> None:
        """Insert one submitted job record."""
        with self.engine.begin() as connection:
            connection.execute(
                jobs_table.insert().values(
                    id=job.id,
                    kind=job.kind,
                    input_json=json.dumps(job.input),
                    created_at=job.created_at,
                    created_by=job.created_by,
                    correlation_id=job.correlation_id,
                    status=job.status,
                    priority=job.priority,
                    schedule_id=job.schedule_id,
                    due_at=job.due_at,
                    lease_owner=job.lease_owner,
                    leased_until=job.leased_until,
                    attempt_count=job.attempt_count,
                    max_retries=job.max_retries,
                    timeout_seconds=job.timeout_seconds,
                    last_error=job.last_error,
                )
            )

    def save_run(self, run: RunRecord) -> None:
        """Insert or replace a run and refresh its artifact and handoff metadata rows."""
        result_json = run.result.model_dump_json() if run.result is not None else None
        with self.engine.begin() as connection:
            connection.execute(
                runs_table.insert().values(
                    id=run.id,
                    job_id=run.job_id,
                    kind=run.kind,
                    attempt=run.attempt,
                    status=run.status,
                    started_at=run.started_at,
                    finished_at=run.finished_at,
                    result_json=result_json,
                    error=run.error,
                    timeout_seconds=run.timeout_seconds,
                    max_retries=run.max_retries,
                    leased_until=run.leased_until,
                )
            )
            if run.result is not None:
                for artifact in run.result.artifacts:
                    connection.execute(
                        artifacts_table.insert().values(
                            run_id=run.id,
                            name=artifact.name,
                            uri=artifact.uri,
                            media_type=artifact.media_type,
                        )
                    )
                for handoff in run.result.handoff:
                    connection.execute(
                        handoffs_table.insert().values(
                            run_id=run.id,
                            kind=handoff.kind,
                            payload_json=json.dumps(handoff.payload),
                        )
                    )

    def save_schedule(self, schedule: ScheduleRecord) -> None:
        """Insert one recurring schedule definition."""
        with self.engine.begin() as connection:
            connection.execute(
                schedules_table.insert().values(
                    id=schedule.id,
                    kind=schedule.kind,
                    input_json=json.dumps(schedule.input),
                    cron=schedule.cron,
                    timezone=schedule.timezone,
                    enabled=1 if schedule.enabled else 0,
                    priority=schedule.priority,
                    created_at=schedule.created_at,
                    next_run_at=schedule.next_run_at,
                    last_materialized_at=schedule.last_materialized_at,
                    max_retries=schedule.max_retries,
                    timeout_seconds=schedule.timeout_seconds,
                )
            )

    def get_schedule(self, schedule_id: str) -> ScheduleRecord | None:
        """Load one schedule by ID for API inspection and updates."""
        with self.engine.connect() as connection:
            row = (
                connection.execute(
                    select(schedules_table).where(schedules_table.c.id == schedule_id)
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            return None
        return _row_to_schedule(dict(row))

    def update_schedule(self, schedule: ScheduleRecord) -> None:
        """Replace mutable fields for one existing schedule."""
        with self.engine.begin() as connection:
            connection.execute(
                update(schedules_table)
                .where(schedules_table.c.id == schedule.id)
                .values(
                    kind=schedule.kind,
                    input_json=json.dumps(schedule.input),
                    cron=schedule.cron,
                    timezone=schedule.timezone,
                    enabled=1 if schedule.enabled else 0,
                    priority=schedule.priority,
                    next_run_at=schedule.next_run_at,
                    last_materialized_at=schedule.last_materialized_at,
                    max_retries=schedule.max_retries,
                    timeout_seconds=schedule.timeout_seconds,
                )
            )

    def list_schedules(self) -> list[ScheduleRecord]:
        """Return all schedules ordered by next run time for CLI display."""
        with self.engine.connect() as connection:
            rows = (
                connection.execute(select(schedules_table).order_by(schedules_table.c.next_run_at))
                .mappings()
                .all()
            )
        return [_row_to_schedule(dict(row)) for row in rows]

    def list_due_schedules(self, now: datetime) -> list[ScheduleRecord]:
        """Return enabled schedules whose next run is due at or before now."""
        with self.engine.connect() as connection:
            rows = (
                connection.execute(
                    select(schedules_table)
                    .where(schedules_table.c.enabled == 1)
                    .where(schedules_table.c.next_run_at <= now)
                    .order_by(schedules_table.c.next_run_at)
                )
                .mappings()
                .all()
            )
        return [_row_to_schedule(dict(row)) for row in rows]

    def update_schedule_after_materialize(
        self,
        schedule_id: str,
        *,
        last_materialized_at: datetime,
        next_run_at: datetime,
    ) -> None:
        """Advance one schedule after its due job has been materialized."""
        with self.engine.begin() as connection:
            connection.execute(
                update(schedules_table)
                .where(schedules_table.c.id == schedule_id)
                .values(last_materialized_at=last_materialized_at, next_run_at=next_run_at)
            )

    def list_jobs(self) -> list[JobRecord]:
        """Return all jobs ordered by creation time for CLI display."""
        with self.engine.connect() as connection:
            rows = (
                connection.execute(select(jobs_table).order_by(jobs_table.c.created_at))
                .mappings()
                .all()
            )
        return [_row_to_job(dict(row)) for row in rows]

    def claim_due_jobs(
        self,
        *,
        worker_id: str,
        now: datetime,
        lease_until: datetime,
        limit: int,
    ) -> list[JobRecord]:
        """Lease due queued or retrying jobs once for this scheduler worker."""
        claimed: list[JobRecord] = []
        with self.engine.begin() as connection:
            claimable_status = or_(
                jobs_table.c.status.in_(["queued", "retrying"]),
                (
                    (jobs_table.c.status == "leased")
                    & (jobs_table.c.leased_until.is_not(None))
                    & (jobs_table.c.leased_until <= now)
                ),
            )
            rows = (
                connection.execute(
                    select(jobs_table)
                    .where(claimable_status)
                    .where(or_(jobs_table.c.due_at.is_(None), jobs_table.c.due_at <= now))
                    .order_by(jobs_table.c.priority.desc(), jobs_table.c.created_at)
                    .limit(limit)
                )
                .mappings()
                .all()
            )
            for row in rows:
                connection.execute(
                    update(jobs_table)
                    .where(jobs_table.c.id == row["id"])
                    .values(
                        status="leased",
                        lease_owner=worker_id,
                        leased_until=lease_until,
                    )
                )
                payload = dict(row)
                payload["status"] = "leased"
                payload["lease_owner"] = worker_id
                payload["leased_until"] = lease_until
                claimed.append(_row_to_job(payload))
        return claimed

    def mark_job_running(self, job_id: str, *, attempt_count: int) -> None:
        """Mark a leased job as running with its incremented attempt count."""
        with self.engine.begin() as connection:
            connection.execute(
                update(jobs_table)
                .where(jobs_table.c.id == job_id)
                .values(status="running", attempt_count=attempt_count)
            )

    def finish_job(
        self,
        job_id: str,
        *,
        status: str,
        last_error: str | None = None,
        due_at: datetime | None = None,
    ) -> None:
        """Finalize or requeue a job after a run attempt."""
        values: dict[str, Any] = {
            "status": status,
            "last_error": last_error,
            "lease_owner": None,
            "leased_until": None,
        }
        if due_at is not None:
            values["due_at"] = due_at
        with self.engine.begin() as connection:
            connection.execute(update(jobs_table).where(jobs_table.c.id == job_id).values(**values))

    def cancel_job(self, job_id: str) -> JobRecord | None:
        """Cancel a non-terminal job and return its updated record."""
        job = self.get_job(job_id)
        if job is None:
            return None
        if job.status in {"completed", "failed", "timed_out", "cancelled"}:
            return job
        with self.engine.begin() as connection:
            connection.execute(
                update(jobs_table)
                .where(jobs_table.c.id == job_id)
                .values(
                    status="cancelled",
                    lease_owner=None,
                    leased_until=None,
                    last_error="cancelled by API",
                )
            )
        return self.get_job(job_id)

    def get_run(self, run_id: str) -> RunRecord | None:
        """Load one run record with its persisted result envelope when present."""
        with self.engine.connect() as connection:
            row = (
                connection.execute(select(runs_table).where(runs_table.c.id == run_id))
                .mappings()
                .one_or_none()
            )
        if row is None:
            return None
        return _row_to_run(dict(row))

    def get_job(self, job_id: str) -> JobRecord | None:
        """Load one job record by ID for tests and CLI inspection."""
        with self.engine.connect() as connection:
            row = (
                connection.execute(select(jobs_table).where(jobs_table.c.id == job_id))
                .mappings()
                .one_or_none()
            )
        if row is None:
            return None
        return _row_to_job(dict(row))

    def list_run_artifacts(self, run_id: str) -> list[ArtifactRecord]:
        """Return artifact metadata rows for one run."""
        with self.engine.connect() as connection:
            rows = (
                connection.execute(
                    select(artifacts_table)
                    .where(artifacts_table.c.run_id == run_id)
                    .order_by(artifacts_table.c.name)
                )
                .mappings()
                .all()
            )
        return [
            ArtifactRecord(
                name=row["name"],
                uri=row["uri"],
                media_type=row["media_type"],
            )
            for row in rows
        ]

    def _ensure_phase2_columns(self) -> None:
        """Add Phase 2 job columns to existing Phase 1 SQLite databases."""
        job_columns = {column["name"] for column in inspect(self.engine).get_columns("jobs")}
        job_additions = {
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
        run_columns = {column["name"] for column in inspect(self.engine).get_columns("runs")}
        run_additions = {
            "timeout_seconds": "INTEGER",
            "max_retries": "INTEGER NOT NULL DEFAULT 0",
            "leased_until": "DATETIME",
        }
        with self.engine.begin() as connection:
            for column_name, ddl in job_additions.items():
                if column_name not in job_columns:
                    connection.execute(text(f"ALTER TABLE jobs ADD COLUMN {column_name} {ddl}"))
            for column_name, ddl in run_additions.items():
                if column_name not in run_columns:
                    connection.execute(text(f"ALTER TABLE runs ADD COLUMN {column_name} {ddl}"))


def _row_to_run(payload: dict[str, Any]) -> RunRecord:
    """Convert a SQLAlchemy row mapping into the public RunRecord contract."""
    result = (
        GoblinResult.model_validate_json(payload["result_json"])
        if payload["result_json"]
        else None
    )
    return RunRecord(
        id=payload["id"],
        job_id=payload["job_id"],
        kind=payload["kind"],
        attempt=payload["attempt"],
        status=payload["status"],
        started_at=_coerce_datetime(payload["started_at"]),
        finished_at=_coerce_datetime(payload["finished_at"]) if payload["finished_at"] else None,
        result=result,
        error=payload["error"],
        timeout_seconds=payload.get("timeout_seconds"),
        max_retries=payload.get("max_retries") or 0,
        leased_until=(
            _coerce_datetime(payload["leased_until"]) if payload.get("leased_until") else None
        ),
    )


def _row_to_job(payload: dict[str, Any]) -> JobRecord:
    """Convert a SQLAlchemy row mapping into the public JobRecord contract."""
    return JobRecord(
        id=payload["id"],
        kind=payload["kind"],
        input=json.loads(payload["input_json"]),
        created_at=_coerce_datetime(payload["created_at"]),
        created_by=payload["created_by"],
        correlation_id=payload["correlation_id"],
        status=payload.get("status") or "queued",
        priority=payload.get("priority") or 100,
        schedule_id=payload.get("schedule_id"),
        due_at=_coerce_datetime(payload["due_at"]) if payload.get("due_at") else None,
        lease_owner=payload.get("lease_owner"),
        leased_until=(
            _coerce_datetime(payload["leased_until"]) if payload.get("leased_until") else None
        ),
        attempt_count=payload.get("attempt_count") or 0,
        max_retries=payload.get("max_retries") or 0,
        timeout_seconds=payload.get("timeout_seconds"),
        last_error=payload.get("last_error"),
    )


def _row_to_schedule(payload: dict[str, Any]) -> ScheduleRecord:
    """Convert a SQLAlchemy row mapping into the public ScheduleRecord contract."""
    return ScheduleRecord(
        id=payload["id"],
        kind=payload["kind"],
        input=json.loads(payload["input_json"]),
        cron=payload["cron"],
        timezone=payload["timezone"],
        enabled=bool(payload["enabled"]),
        priority=payload["priority"],
        created_at=_coerce_datetime(payload["created_at"]),
        next_run_at=_coerce_datetime(payload["next_run_at"]),
        last_materialized_at=(
            _coerce_datetime(payload["last_materialized_at"])
            if payload.get("last_materialized_at")
            else None
        ),
        max_retries=payload["max_retries"],
        timeout_seconds=payload.get("timeout_seconds"),
    )


def _coerce_datetime(value: datetime | str) -> datetime:
    """Normalize SQLite-returned timestamp values for Pydantic models."""
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


__all__ = [
    "ArtifactRecord",
    "DEFAULT_DB_PATH",
    "HandoffRecord",
    "SQLiteStore",
]
