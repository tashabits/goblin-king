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
    ApiTokenRecord,
    ArtifactRecord,
    AuditLogRecord,
    EventRecord,
    FanoutRecord,
    GoblinResult,
    HandoffRecord,
    HeartbeatRecord,
    JobRecord,
    LongServiceRecord,
    MembershipRecord,
    ProjectRecord,
    RateLimitRecord,
    RunRecord,
    ScheduleRecord,
    TeamRecord,
    UserRecord,
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
    Column("project_id", String, nullable=True),
    Column("fanout_id", String, nullable=True),
    Column("metadata_json", Text, nullable=False, default="{}"),
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

fanouts_table = Table(
    "fanouts",
    metadata,
    Column("id", String, primary_key=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("created_by", String, nullable=False),
    Column("project_id", String, nullable=True),
    Column("correlation_id", String, nullable=True),
    Column("description", Text, nullable=True),
)

schedules_table = Table(
    "schedules",
    metadata,
    Column("id", String, primary_key=True),
    Column("kind", String, nullable=False),
    Column("project_id", String, nullable=True),
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
    Column("project_id", String, nullable=True),
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

events_table = Table(
    "events",
    metadata,
    Column("id", String, primary_key=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("event_type", String, nullable=False),
    Column("source", String, nullable=False),
    Column("project_id", String, nullable=True),
    Column("job_id", String, nullable=True),
    Column("run_id", String, nullable=True),
    Column("fanout_id", String, nullable=True),
    Column("schedule_id", String, nullable=True),
    Column("worker_id", String, nullable=True),
    Column("scheduler_id", String, nullable=True),
    Column("payload_json", Text, nullable=False, default="{}"),
)

users_table = Table(
    "users",
    metadata,
    Column("id", String, primary_key=True),
    Column("email", String, nullable=False),
    Column("display_name", String, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("disabled", Integer, nullable=False, default=0),
)

teams_table = Table(
    "teams",
    metadata,
    Column("id", String, primary_key=True),
    Column("name", String, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

projects_table = Table(
    "projects",
    metadata,
    Column("id", String, primary_key=True),
    Column("name", String, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

memberships_table = Table(
    "memberships",
    metadata,
    Column("id", String, primary_key=True),
    Column("project_id", String, nullable=False),
    Column("role", String, nullable=False),
    Column("user_id", String, nullable=True),
    Column("team_id", String, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

api_tokens_table = Table(
    "api_tokens",
    metadata,
    Column("id", String, primary_key=True),
    Column("name", String, nullable=False),
    Column("token_hash", String, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("user_id", String, nullable=False),
    Column("project_id", String, nullable=True),
    Column("role", String, nullable=False),
    Column("revoked_at", DateTime(timezone=True), nullable=True),
)

audit_logs_table = Table(
    "audit_logs",
    metadata,
    Column("id", String, primary_key=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("action", String, nullable=False),
    Column("outcome", String, nullable=False),
    Column("user_id", String, nullable=True),
    Column("token_id", String, nullable=True),
    Column("project_id", String, nullable=True),
    Column("resource_type", String, nullable=True),
    Column("resource_id", String, nullable=True),
    Column("detail_json", Text, nullable=False, default="{}"),
)

rate_limits_table = Table(
    "rate_limits",
    metadata,
    Column("key", String, primary_key=True),
    Column("window_started_at", DateTime(timezone=True), nullable=False),
    Column("count", Integer, nullable=False, default=0),
)

heartbeats_table = Table(
    "heartbeats",
    metadata,
    Column("owner_id", String, primary_key=True),
    Column("owner_type", String, nullable=False),
    Column("status", String, nullable=False),
    Column("last_seen_at", DateTime(timezone=True), nullable=False),
    Column("job_id", String, nullable=True),
    Column("run_id", String, nullable=True),
    Column("payload_json", Text, nullable=False, default="{}"),
)

long_services_table = Table(
    "long_services",
    metadata,
    Column("id", String, primary_key=True),
    Column("kind", String, nullable=False),
    Column("project_id", String, nullable=True),
    Column("image", String, nullable=True),
    Column("base_url", Text, nullable=False),
    Column("status", String, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("created_by", String, nullable=False),
    Column("last_probe_at", DateTime(timezone=True), nullable=True),
    Column("last_probe_json", Text, nullable=True),
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
                    project_id=job.project_id,
                    fanout_id=job.fanout_id,
                    metadata_json=json.dumps(job.metadata),
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

    def save_event(self, event: EventRecord) -> None:
        """Insert one durable event record."""
        with self.engine.begin() as connection:
            connection.execute(
                events_table.insert().values(
                    id=event.id,
                    created_at=event.created_at,
                    event_type=event.event_type,
                    source=event.source,
                    project_id=event.project_id,
                    job_id=event.job_id,
                    run_id=event.run_id,
                    fanout_id=event.fanout_id,
                    schedule_id=event.schedule_id,
                    worker_id=event.worker_id,
                    scheduler_id=event.scheduler_id,
                    payload_json=json.dumps(event.payload),
                )
            )

    def list_events(
        self,
        *,
        limit: int = 100,
        event_type: str | None = None,
        after_id: str | None = None,
        job_id: str | None = None,
        run_id: str | None = None,
        fanout_id: str | None = None,
        schedule_id: str | None = None,
        worker_id: str | None = None,
        scheduler_id: str | None = None,
        project_id: str | None = None,
        offset: int = 0,
    ) -> list[EventRecord]:
        """Return durable events with simple bounded filtering."""
        bounded_limit = max(1, min(limit, 500))
        with self.engine.connect() as connection:
            query = select(events_table).order_by(events_table.c.created_at, events_table.c.id)
            if event_type is not None:
                query = query.where(events_table.c.event_type == event_type)
            if project_id is not None:
                query = query.where(events_table.c.project_id == project_id)
            if after_id is not None:
                cursor = connection.execute(
                    select(events_table.c.created_at).where(events_table.c.id == after_id)
                ).scalar_one_or_none()
                if cursor is not None:
                    query = query.where(events_table.c.created_at > cursor)
            for column_name, value in {
                "job_id": job_id,
                "run_id": run_id,
                "fanout_id": fanout_id,
                "schedule_id": schedule_id,
                "worker_id": worker_id,
                "scheduler_id": scheduler_id,
            }.items():
                if value is not None:
                    query = query.where(getattr(events_table.c, column_name) == value)
            rows = (
                connection.execute(query.offset(max(offset, 0)).limit(bounded_limit))
                .mappings()
                .all()
            )
        return [_row_to_event(dict(row)) for row in rows]

    def count_events(self, *, project_id: str | None = None) -> int:
        """Return a simple event count for pagination metadata."""
        with self.engine.connect() as connection:
            query = select(events_table.c.id)
            if project_id is not None:
                query = query.where(events_table.c.project_id == project_id)
            return len(connection.execute(query).all())

    def save_user(self, user: UserRecord) -> None:
        """Insert one local API user."""
        with self.engine.begin() as connection:
            connection.execute(
                users_table.insert().values(
                    id=user.id,
                    email=user.email,
                    display_name=user.display_name,
                    created_at=user.created_at,
                    disabled=1 if user.disabled else 0,
                )
            )

    def save_team(self, team: TeamRecord) -> None:
        """Insert one local API team."""
        with self.engine.begin() as connection:
            connection.execute(
                teams_table.insert().values(
                    id=team.id,
                    name=team.name,
                    created_at=team.created_at,
                )
            )

    def save_project(self, project: ProjectRecord) -> None:
        """Insert one local project boundary."""
        with self.engine.begin() as connection:
            connection.execute(
                projects_table.insert().values(
                    id=project.id,
                    name=project.name,
                    created_at=project.created_at,
                )
            )

    def save_membership(self, membership: MembershipRecord) -> None:
        """Insert one project membership grant."""
        with self.engine.begin() as connection:
            connection.execute(
                memberships_table.insert().values(
                    id=membership.id,
                    project_id=membership.project_id,
                    role=membership.role,
                    user_id=membership.user_id,
                    team_id=membership.team_id,
                    created_at=membership.created_at,
                )
            )

    def save_api_token(self, token: ApiTokenRecord) -> None:
        """Insert one hashed API token record."""
        with self.engine.begin() as connection:
            connection.execute(
                api_tokens_table.insert().values(
                    id=token.id,
                    name=token.name,
                    token_hash=token.token_hash,
                    created_at=token.created_at,
                    user_id=token.user_id,
                    project_id=token.project_id,
                    role=token.role,
                    revoked_at=token.revoked_at,
                )
            )

    def get_api_token_by_hash(self, token_hash: str) -> ApiTokenRecord | None:
        """Load one non-revoked API token by its stored hash."""
        with self.engine.connect() as connection:
            row = (
                connection.execute(
                    select(api_tokens_table)
                    .where(api_tokens_table.c.token_hash == token_hash)
                    .where(api_tokens_table.c.revoked_at.is_(None))
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            return None
        return _row_to_api_token(dict(row))

    def revoke_api_token(self, token_id: str, revoked_at: datetime) -> ApiTokenRecord | None:
        """Revoke an API token and return the updated token."""
        with self.engine.begin() as connection:
            connection.execute(
                update(api_tokens_table)
                .where(api_tokens_table.c.id == token_id)
                .values(revoked_at=revoked_at)
            )
        with self.engine.connect() as connection:
            row = (
                connection.execute(
                    select(api_tokens_table).where(api_tokens_table.c.id == token_id)
                )
                .mappings()
                .one_or_none()
            )
        return _row_to_api_token(dict(row)) if row else None

    def get_user(self, user_id: str) -> UserRecord | None:
        """Load one user by ID."""
        with self.engine.connect() as connection:
            row = (
                connection.execute(select(users_table).where(users_table.c.id == user_id))
                .mappings()
                .one_or_none()
            )
        return _row_to_user(dict(row)) if row else None

    def get_project(self, project_id: str) -> ProjectRecord | None:
        """Load one project by ID."""
        with self.engine.connect() as connection:
            row = (
                connection.execute(select(projects_table).where(projects_table.c.id == project_id))
                .mappings()
                .one_or_none()
            )
        return _row_to_project(dict(row)) if row else None

    def list_projects(self) -> list[ProjectRecord]:
        """Return all local projects."""
        with self.engine.connect() as connection:
            rows = (
                connection.execute(select(projects_table).order_by(projects_table.c.name))
                .mappings()
                .all()
            )
        return [_row_to_project(dict(row)) for row in rows]

    def save_audit_log(self, audit: AuditLogRecord) -> None:
        """Insert one audit log event."""
        with self.engine.begin() as connection:
            connection.execute(
                audit_logs_table.insert().values(
                    id=audit.id,
                    created_at=audit.created_at,
                    action=audit.action,
                    outcome=audit.outcome,
                    user_id=audit.user_id,
                    token_id=audit.token_id,
                    project_id=audit.project_id,
                    resource_type=audit.resource_type,
                    resource_id=audit.resource_id,
                    detail_json=json.dumps(audit.detail),
                )
            )

    def list_audit_logs(
        self,
        *,
        project_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AuditLogRecord]:
        """Return audit log rows with bounded pagination."""
        with self.engine.connect() as connection:
            query = select(audit_logs_table).order_by(audit_logs_table.c.created_at)
            if project_id is not None:
                query = query.where(audit_logs_table.c.project_id == project_id)
            rows = (
                connection.execute(query.offset(max(offset, 0)).limit(max(1, min(limit, 500))))
                .mappings()
                .all()
            )
        return [_row_to_audit_log(dict(row)) for row in rows]

    def increment_rate_limit(
        self,
        *,
        key: str,
        window_started_at: datetime,
        reset_existing: bool,
    ) -> RateLimitRecord:
        """Increment one rate-limit counter and return the current window state."""
        with self.engine.begin() as connection:
            row = (
                connection.execute(select(rate_limits_table).where(rate_limits_table.c.key == key))
                .mappings()
                .one_or_none()
            )
            if row is None:
                count = 1
                connection.execute(
                    rate_limits_table.insert().values(
                        key=key,
                        window_started_at=window_started_at,
                        count=count,
                    )
                )
            elif reset_existing:
                count = 1
                connection.execute(
                    update(rate_limits_table)
                    .where(rate_limits_table.c.key == key)
                    .values(window_started_at=window_started_at, count=count)
                )
            else:
                count = row["count"] + 1
                window_started_at = _coerce_datetime(row["window_started_at"])
                connection.execute(
                    update(rate_limits_table)
                    .where(rate_limits_table.c.key == key)
                    .values(count=count)
                )
        return RateLimitRecord(key=key, window_started_at=window_started_at, count=count)

    def upsert_heartbeat(self, heartbeat: HeartbeatRecord) -> None:
        """Insert or replace the latest heartbeat for one scheduler or worker owner."""
        with self.engine.begin() as connection:
            existing = connection.execute(
                select(heartbeats_table.c.owner_id).where(
                    heartbeats_table.c.owner_id == heartbeat.owner_id
                )
            ).scalar_one_or_none()
            values = {
                "owner_id": heartbeat.owner_id,
                "owner_type": heartbeat.owner_type,
                "status": heartbeat.status,
                "last_seen_at": heartbeat.last_seen_at,
                "job_id": heartbeat.job_id,
                "run_id": heartbeat.run_id,
                "payload_json": json.dumps(heartbeat.payload),
            }
            if existing is None:
                connection.execute(heartbeats_table.insert().values(**values))
            else:
                connection.execute(
                    update(heartbeats_table)
                    .where(heartbeats_table.c.owner_id == heartbeat.owner_id)
                    .values(**values)
                )

    def list_heartbeats(self) -> list[HeartbeatRecord]:
        """Return all scheduler and worker heartbeats ordered by last seen time."""
        with self.engine.connect() as connection:
            rows = (
                connection.execute(
                    select(heartbeats_table).order_by(heartbeats_table.c.last_seen_at)
                )
                .mappings()
                .all()
            )
        return [_row_to_heartbeat(dict(row)) for row in rows]

    def get_heartbeat(self, owner_id: str) -> HeartbeatRecord | None:
        """Load one heartbeat by owner ID."""
        with self.engine.connect() as connection:
            row = (
                connection.execute(
                    select(heartbeats_table).where(heartbeats_table.c.owner_id == owner_id)
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            return None
        return _row_to_heartbeat(dict(row))

    def save_long_service(self, service: LongServiceRecord) -> None:
        """Insert one registered long-running service goblin."""
        with self.engine.begin() as connection:
            connection.execute(
                long_services_table.insert().values(
                    id=service.id,
                    kind=service.kind,
                    project_id=service.project_id,
                    image=service.image,
                    base_url=service.base_url,
                    status=service.status,
                    created_at=service.created_at,
                    created_by=service.created_by,
                    last_probe_at=service.last_probe_at,
                    last_probe_json=(
                        json.dumps(service.last_probe_json)
                        if service.last_probe_json is not None
                        else None
                    ),
                )
            )

    def get_long_service(self, service_id: str) -> LongServiceRecord | None:
        """Load one long-running service goblin by ID."""
        with self.engine.connect() as connection:
            row = (
                connection.execute(
                    select(long_services_table).where(long_services_table.c.id == service_id)
                )
                .mappings()
                .one_or_none()
            )
        return _row_to_long_service(dict(row)) if row else None

    def list_long_services(self, *, project_id: str | None = None) -> list[LongServiceRecord]:
        """Return registered long-running service goblins ordered by creation time."""
        with self.engine.connect() as connection:
            query = select(long_services_table).order_by(long_services_table.c.created_at)
            if project_id is not None:
                query = query.where(long_services_table.c.project_id == project_id)
            rows = connection.execute(query).mappings().all()
        return [_row_to_long_service(dict(row)) for row in rows]

    def update_long_service_probe(
        self,
        service_id: str,
        *,
        status: str,
        last_probe_at: datetime,
        last_probe_json: dict[str, Any],
    ) -> LongServiceRecord | None:
        """Persist the latest probe result for a long-running service goblin."""
        with self.engine.begin() as connection:
            connection.execute(
                update(long_services_table)
                .where(long_services_table.c.id == service_id)
                .values(
                    status=status,
                    last_probe_at=last_probe_at,
                    last_probe_json=json.dumps(last_probe_json),
                )
            )
        return self.get_long_service(service_id)

    def save_fanout(self, fanout: FanoutRecord) -> None:
        """Insert one durable fanout batch record."""
        with self.engine.begin() as connection:
            connection.execute(
                fanouts_table.insert().values(
                    id=fanout.id,
                    created_at=fanout.created_at,
                    created_by=fanout.created_by,
                    project_id=fanout.project_id,
                    correlation_id=fanout.correlation_id,
                    description=fanout.description,
                )
            )

    def get_fanout(self, fanout_id: str) -> FanoutRecord | None:
        """Load one fanout batch record by ID."""
        with self.engine.connect() as connection:
            row = (
                connection.execute(select(fanouts_table).where(fanouts_table.c.id == fanout_id))
                .mappings()
                .one_or_none()
            )
        if row is None:
            return None
        return _row_to_fanout(dict(row))

    def list_fanouts(self) -> list[FanoutRecord]:
        """Return all fanout batches ordered by creation time."""
        with self.engine.connect() as connection:
            rows = (
                connection.execute(select(fanouts_table).order_by(fanouts_table.c.created_at))
                .mappings()
                .all()
            )
        return [_row_to_fanout(dict(row)) for row in rows]

    def list_fanout_jobs(self, fanout_id: str) -> list[JobRecord]:
        """Return child jobs for one fanout batch ordered by item index."""
        with self.engine.connect() as connection:
            rows = (
                connection.execute(
                    select(jobs_table)
                    .where(jobs_table.c.fanout_id == fanout_id)
                    .order_by(jobs_table.c.created_at)
                )
                .mappings()
                .all()
            )
        return [_row_to_job(dict(row)) for row in rows]

    def list_job_runs(self, job_id: str) -> list[RunRecord]:
        """Return runs for one job ordered by attempt."""
        with self.engine.connect() as connection:
            rows = (
                connection.execute(
                    select(runs_table)
                    .where(runs_table.c.job_id == job_id)
                    .order_by(runs_table.c.attempt)
                )
                .mappings()
                .all()
            )
        return [_row_to_run(dict(row)) for row in rows]

    def save_run(self, run: RunRecord) -> None:
        """Insert or replace a run and refresh its artifact and handoff metadata rows."""
        result_json = run.result.model_dump_json() if run.result is not None else None
        with self.engine.begin() as connection:
            connection.execute(
                runs_table.insert().values(
                    id=run.id,
                    job_id=run.job_id,
                    kind=run.kind,
                    project_id=run.project_id,
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
                    project_id=schedule.project_id,
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
                    project_id=schedule.project_id,
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

    def list_jobs_page(
        self,
        *,
        project_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[JobRecord]:
        """Return a bounded page of jobs for API responses."""
        with self.engine.connect() as connection:
            query = select(jobs_table).order_by(jobs_table.c.created_at)
            if project_id is not None:
                query = query.where(jobs_table.c.project_id == project_id)
            if status is not None:
                query = query.where(jobs_table.c.status == status)
            rows = (
                connection.execute(query.offset(max(offset, 0)).limit(max(1, min(limit, 500))))
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
        run_columns = {column["name"] for column in inspect(self.engine).get_columns("runs")}
        run_additions = {
            "project_id": "TEXT",
            "timeout_seconds": "INTEGER",
            "max_retries": "INTEGER NOT NULL DEFAULT 0",
            "leased_until": "DATETIME",
        }
        fanout_columns = {column["name"] for column in inspect(self.engine).get_columns("fanouts")}
        schedule_columns = {
            column["name"] for column in inspect(self.engine).get_columns("schedules")
        }
        event_columns = {column["name"] for column in inspect(self.engine).get_columns("events")}
        with self.engine.begin() as connection:
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
        project_id=payload.get("project_id"),
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
        project_id=payload.get("project_id"),
        fanout_id=payload.get("fanout_id"),
        metadata=json.loads(payload.get("metadata_json") or "{}"),
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


def _row_to_fanout(payload: dict[str, Any]) -> FanoutRecord:
    """Convert a SQLAlchemy row mapping into the public FanoutRecord contract."""
    return FanoutRecord(
        id=payload["id"],
        created_at=_coerce_datetime(payload["created_at"]),
        created_by=payload["created_by"],
        project_id=payload.get("project_id"),
        correlation_id=payload.get("correlation_id"),
        description=payload.get("description"),
    )


def _row_to_schedule(payload: dict[str, Any]) -> ScheduleRecord:
    """Convert a SQLAlchemy row mapping into the public ScheduleRecord contract."""
    return ScheduleRecord(
        id=payload["id"],
        kind=payload["kind"],
        project_id=payload.get("project_id"),
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


def _row_to_event(payload: dict[str, Any]) -> EventRecord:
    """Convert a SQLAlchemy row mapping into the public EventRecord contract."""
    return EventRecord(
        id=payload["id"],
        created_at=_coerce_datetime(payload["created_at"]),
        event_type=payload["event_type"],
        source=payload["source"],
        project_id=payload.get("project_id"),
        job_id=payload.get("job_id"),
        run_id=payload.get("run_id"),
        fanout_id=payload.get("fanout_id"),
        schedule_id=payload.get("schedule_id"),
        worker_id=payload.get("worker_id"),
        scheduler_id=payload.get("scheduler_id"),
        payload=json.loads(payload.get("payload_json") or "{}"),
    )


def _row_to_user(payload: dict[str, Any]) -> UserRecord:
    """Convert a SQLAlchemy row mapping into a UserRecord."""
    return UserRecord(
        id=payload["id"],
        email=payload["email"],
        display_name=payload["display_name"],
        created_at=_coerce_datetime(payload["created_at"]),
        disabled=bool(payload["disabled"]),
    )


def _row_to_project(payload: dict[str, Any]) -> ProjectRecord:
    """Convert a SQLAlchemy row mapping into a ProjectRecord."""
    return ProjectRecord(
        id=payload["id"],
        name=payload["name"],
        created_at=_coerce_datetime(payload["created_at"]),
    )


def _row_to_api_token(payload: dict[str, Any]) -> ApiTokenRecord:
    """Convert a SQLAlchemy row mapping into an ApiTokenRecord."""
    return ApiTokenRecord(
        id=payload["id"],
        name=payload["name"],
        token_hash=payload["token_hash"],
        created_at=_coerce_datetime(payload["created_at"]),
        user_id=payload["user_id"],
        project_id=payload.get("project_id"),
        role=payload["role"],
        revoked_at=_coerce_datetime(payload["revoked_at"]) if payload.get("revoked_at") else None,
    )


def _row_to_audit_log(payload: dict[str, Any]) -> AuditLogRecord:
    """Convert a SQLAlchemy row mapping into an AuditLogRecord."""
    return AuditLogRecord(
        id=payload["id"],
        created_at=_coerce_datetime(payload["created_at"]),
        action=payload["action"],
        outcome=payload["outcome"],
        user_id=payload.get("user_id"),
        token_id=payload.get("token_id"),
        project_id=payload.get("project_id"),
        resource_type=payload.get("resource_type"),
        resource_id=payload.get("resource_id"),
        detail=json.loads(payload.get("detail_json") or "{}"),
    )


def _row_to_heartbeat(payload: dict[str, Any]) -> HeartbeatRecord:
    """Convert a SQLAlchemy row mapping into the public HeartbeatRecord contract."""
    return HeartbeatRecord(
        owner_id=payload["owner_id"],
        owner_type=payload["owner_type"],
        status=payload["status"],
        last_seen_at=_coerce_datetime(payload["last_seen_at"]),
        job_id=payload.get("job_id"),
        run_id=payload.get("run_id"),
        payload=json.loads(payload.get("payload_json") or "{}"),
    )


def _row_to_long_service(payload: dict[str, Any]) -> LongServiceRecord:
    """Convert a SQLAlchemy row mapping into a LongServiceRecord."""
    return LongServiceRecord(
        id=payload["id"],
        kind=payload["kind"],
        project_id=payload.get("project_id"),
        image=payload.get("image"),
        base_url=payload["base_url"],
        status=payload["status"],
        created_at=_coerce_datetime(payload["created_at"]),
        created_by=payload["created_by"],
        last_probe_at=(
            _coerce_datetime(payload["last_probe_at"]) if payload.get("last_probe_at") else None
        ),
        last_probe_json=(
            json.loads(payload["last_probe_json"]) if payload.get("last_probe_json") else None
        ),
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
    "ApiTokenRecord",
    "AuditLogRecord",
    "DEFAULT_DB_PATH",
    "EventRecord",
    "FanoutRecord",
    "HeartbeatRecord",
    "HandoffRecord",
    "LongServiceRecord",
    "SQLiteStore",
]
