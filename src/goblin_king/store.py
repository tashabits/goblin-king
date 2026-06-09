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
    select,
)
from sqlalchemy.engine import Engine

from goblin_king.contracts import (
    ArtifactRecord,
    GoblinResult,
    HandoffRecord,
    JobRecord,
    RunRecord,
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
        payload = dict(row)
        return JobRecord(
            id=payload["id"],
            kind=payload["kind"],
            input=json.loads(payload["input_json"]),
            created_at=_coerce_datetime(payload["created_at"]),
            created_by=payload["created_by"],
            correlation_id=payload["correlation_id"],
        )


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
