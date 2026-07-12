"""SQLite persistence for Phase 1 job and run records."""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, delete, func, or_, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError

from goblin_king.contracts import (
    ApiTokenRecord,
    ArtifactRecord,
    AuditLogRecord,
    DeploymentRecord,
    EventRecord,
    FanoutRecord,
    HandoffRecord,
    HeartbeatRecord,
    ImagePromotionRecord,
    JobRecord,
    LongServiceRecord,
    MembershipRecord,
    NotebookGoblinRecord,
    NotebookServiceRecord,
    ProjectRecord,
    RateLimitRecord,
    RepositoryEntryRecord,
    RepositoryVersionRecord,
    RunRecord,
    ScheduleRecord,
    TeamRecord,
    UserRecord,
    WorkerValidationRecord,
)
from goblin_king.store_migrations import ensure_schema_columns
from goblin_king.store_rows import (
    _coerce_datetime,
    _row_to_api_token,
    _row_to_audit_log,
    _row_to_deployment_record,
    _row_to_event,
    _row_to_fanout,
    _row_to_heartbeat,
    _row_to_image_promotion,
    _row_to_job,
    _row_to_long_service,
    _row_to_notebook_goblin,
    _row_to_notebook_service,
    _row_to_project,
    _row_to_repository_entry,
    _row_to_repository_version,
    _row_to_run,
    _row_to_schedule,
    _row_to_user,
    _row_to_worker_validation,
)
from goblin_king.store_schema import (
    api_tokens_table,
    artifacts_table,
    audit_logs_table,
    deployment_records_table,
    events_table,
    fanouts_table,
    handoffs_table,
    heartbeats_table,
    image_promotions_table,
    jobs_table,
    long_services_table,
    memberships_table,
    metadata,
    notebook_goblins_table,
    notebook_services_table,
    projects_table,
    rate_limits_table,
    repository_entries_table,
    repository_versions_table,
    runs_table,
    schedules_table,
    teams_table,
    users_table,
    worker_validations_table,
)

DEFAULT_DB_PATH = Path(".goblin-king") / "goblin-king.sqlite3"
REPOSITORY_STATUS_TRANSITIONS = {
    "draft": {"validated", "rejected", "retired"},
    "validated": {"pending_review", "rejected", "retired"},
    "pending_review": {"approved", "rejected", "retired"},
    "approved": {"published", "rejected", "retired"},
    "published": {"retired"},
    "rejected": {"retired"},
    "retired": set(),
}
REPOSITORY_VERSION_IDENTITY_FIELDS = {
    "entry_id",
    "version",
    "kind",
    "source_hash",
    "runner_image",
    "created_at",
}


def _initialize_schema(engine: Engine) -> None:
    """Create or migrate SQLite schema, tolerating parallel first startup."""
    retryable = ("already exists", "duplicate column name", "database is locked")
    last_error: OperationalError | None = None
    for attempt in range(10):
        try:
            metadata.create_all(engine)
            ensure_schema_columns(engine)
            return
        except OperationalError as error:
            message = str(error).lower()
            if not any(token in message for token in retryable):
                raise
            last_error = error
            time.sleep(0.1 * (attempt + 1))
    if last_error is not None:
        raise last_error


class SQLiteStore:
    """Persist Phase 1 jobs, runs, artifacts, and handoffs in a local SQLite database."""

    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.engine: Engine = create_engine(f"sqlite:///{self.db_path}")
        _initialize_schema(self.engine)

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

    def save_worker_validation(self, validation: WorkerValidationRecord) -> None:
        """Insert one worker contract validation record."""
        with self.engine.begin() as connection:
            connection.execute(
                worker_validations_table.insert().values(
                    id=validation.id,
                    kind=validation.kind,
                    image=validation.image,
                    image_digest=validation.image_digest,
                    contract_version=validation.contract_version,
                    validator_version=validation.validator_version,
                    validated_at=validation.validated_at,
                    status=validation.status,
                    failure_reasons_json=json.dumps(validation.failure_reasons),
                    effective_policy_json=json.dumps(validation.effective_policy),
                )
            )

    def save_notebook_goblin(self, record: NotebookGoblinRecord) -> None:
        """Insert or replace one notebook-defined Python function goblin."""
        values = {
            "kind": record.kind,
            "project_id": record.project_id,
            "display_name": record.display_name,
            "image": record.image,
            "source": record.source,
            "source_hash": record.source_hash,
            "function_name": record.function_name,
            "timeout_seconds": record.timeout_seconds,
            "max_retries": record.max_retries,
            "created_at": record.created_at,
            "updated_at": record.updated_at,
            "created_by": record.created_by,
            "metadata_json": json.dumps(record.metadata),
        }
        with self.engine.begin() as connection:
            existing = connection.execute(
                select(notebook_goblins_table.c.created_at).where(
                    notebook_goblins_table.c.kind == record.kind
                )
            ).scalar_one_or_none()
            if existing is None:
                connection.execute(notebook_goblins_table.insert().values(**values))
            else:
                values["created_at"] = existing
                connection.execute(
                    update(notebook_goblins_table)
                    .where(notebook_goblins_table.c.kind == record.kind)
                    .values(**values)
                )

    def get_notebook_goblin(self, kind: str) -> NotebookGoblinRecord | None:
        """Return one notebook-defined goblin by kind."""
        with self.engine.begin() as connection:
            row = connection.execute(
                select(notebook_goblins_table).where(notebook_goblins_table.c.kind == kind)
            ).first()
        return _row_to_notebook_goblin(dict(row._mapping)) if row else None

    def list_notebook_goblins(
        self,
        *,
        project_id: str | None = None,
    ) -> list[NotebookGoblinRecord]:
        """Return notebook-defined goblins visible to API and scheduler surfaces."""
        query = select(notebook_goblins_table).order_by(notebook_goblins_table.c.kind)
        if project_id is not None:
            query = query.where(notebook_goblins_table.c.project_id == project_id)
        with self.engine.begin() as connection:
            rows = connection.execute(query).fetchall()
        return [_row_to_notebook_goblin(dict(row._mapping)) for row in rows]

    def save_notebook_service(self, record: NotebookServiceRecord) -> None:
        """Insert or replace one notebook-defined ASGI service bundle."""
        values = {
            "kind": record.kind,
            "project_id": record.project_id,
            "display_name": record.display_name,
            "image": record.image,
            "source": record.source,
            "source_hash": record.source_hash,
            "app_name": record.app_name,
            "requirements_json": json.dumps(record.requirements),
            "port": record.port,
            "probe_path": record.probe_path,
            "created_at": record.created_at,
            "updated_at": record.updated_at,
            "created_by": record.created_by,
            "metadata_json": json.dumps(record.metadata),
            "runtime_backend": record.runtime_backend,
            "runtime_name": record.runtime_name,
            "runtime_status": record.runtime_status,
            "active_service_id": record.active_service_id,
        }
        with self.engine.begin() as connection:
            existing = connection.execute(
                select(notebook_services_table.c.created_at).where(
                    notebook_services_table.c.kind == record.kind
                )
            ).scalar_one_or_none()
            if existing is None:
                connection.execute(notebook_services_table.insert().values(**values))
            else:
                values["created_at"] = existing
                connection.execute(
                    update(notebook_services_table)
                    .where(notebook_services_table.c.kind == record.kind)
                    .values(**values)
                )

    def get_notebook_service(self, kind: str) -> NotebookServiceRecord | None:
        """Return one notebook-defined ASGI service by kind."""
        with self.engine.begin() as connection:
            row = connection.execute(
                select(notebook_services_table).where(notebook_services_table.c.kind == kind)
            ).first()
        return _row_to_notebook_service(dict(row._mapping)) if row else None

    def list_notebook_services(
        self,
        *,
        project_id: str | None = None,
    ) -> list[NotebookServiceRecord]:
        """Return notebook-defined ASGI services visible to API surfaces."""
        query = select(notebook_services_table).order_by(notebook_services_table.c.kind)
        if project_id is not None:
            query = query.where(notebook_services_table.c.project_id == project_id)
        with self.engine.begin() as connection:
            rows = connection.execute(query).fetchall()
        return [_row_to_notebook_service(dict(row._mapping)) for row in rows]

    def update_notebook_service_runtime(
        self,
        kind: str,
        *,
        runtime_status: str,
        runtime_backend: str | None = None,
        runtime_name: str | None = None,
        active_service_id: str | None = None,
        updated_at: datetime | None = None,
    ) -> NotebookServiceRecord | None:
        """Persist managed runtime status for a notebook-defined ASGI service."""
        values = {
            "runtime_status": runtime_status,
            "runtime_backend": runtime_backend,
            "runtime_name": runtime_name,
            "active_service_id": active_service_id,
            "updated_at": updated_at,
        }
        if updated_at is None:
            values.pop("updated_at")
        with self.engine.begin() as connection:
            connection.execute(
                update(notebook_services_table)
                .where(notebook_services_table.c.kind == kind)
                .values(**values)
            )
        return self.get_notebook_service(kind)

    def create_repository_entry(
        self,
        entry: RepositoryEntryRecord,
    ) -> RepositoryEntryRecord:
        if entry.status != "draft":
            raise ValueError("repository entries must start as drafts")
        if entry.published_version is not None:
            raise ValueError("draft repository entries cannot be published")
        with self.engine.begin() as connection:
            _ensure_repository_entry_name_available(
                connection,
                entry.project_id,
                entry.name,
            )
            connection.execute(
                repository_entries_table.insert().values(
                    **_repository_entry_values(entry)
                )
            )
        loaded = self.get_repository_entry(entry.id)
        assert loaded is not None
        return loaded

    def update_repository_entry(
        self,
        entry: RepositoryEntryRecord,
    ) -> RepositoryEntryRecord:
        existing = self.get_repository_entry(entry.id)
        if existing is None:
            raise ValueError(f"repository entry not found: {entry.id}")
        _validate_repository_transition(existing.status, entry.status)
        if entry.status == "published" and entry.published_version is None:
            raise ValueError("published repository entries require a published version")
        with self.engine.begin() as connection:
            if (entry.project_id, entry.name) != (existing.project_id, existing.name):
                _ensure_repository_entry_name_available(
                    connection,
                    entry.project_id,
                    entry.name,
                    exclude_entry_id=entry.id,
                )
            if entry.published_version is not None:
                _require_repository_version_status(
                    connection,
                    entry.id,
                    entry.published_version,
                    "published",
                )
            connection.execute(
                update(repository_entries_table)
                .where(repository_entries_table.c.id == entry.id)
                .values(**_repository_entry_values(entry))
            )
        loaded = self.get_repository_entry(entry.id)
        assert loaded is not None
        return loaded

    def get_repository_entry(self, entry_id: str) -> RepositoryEntryRecord | None:
        with self.engine.connect() as connection:
            row = (
                connection.execute(
                    select(repository_entries_table).where(
                        repository_entries_table.c.id == entry_id
                    )
                )
                .mappings()
                .one_or_none()
            )
        return _row_to_repository_entry(dict(row)) if row else None

    def get_repository_entry_by_project_name(
        self,
        project_id: str | None,
        name: str,
        *,
        include_retired: bool = False,
    ) -> RepositoryEntryRecord | None:
        query = select(repository_entries_table).where(repository_entries_table.c.name == name)
        if project_id is None:
            query = query.where(repository_entries_table.c.project_id.is_(None))
        else:
            query = query.where(repository_entries_table.c.project_id == project_id)
        if not include_retired:
            query = query.where(repository_entries_table.c.status != "retired")
        query = query.order_by(repository_entries_table.c.updated_at.desc())
        with self.engine.connect() as connection:
            row = connection.execute(query).mappings().first()
        return _row_to_repository_entry(dict(row)) if row else None

    def list_repository_entries(
        self,
        *,
        project_id: str | None = None,
        status: str | None = None,
        entry_type: str | None = None,
        include_retired: bool = True,
    ) -> list[RepositoryEntryRecord]:
        query = select(repository_entries_table).order_by(
            repository_entries_table.c.project_id,
            repository_entries_table.c.name,
        )
        if project_id is not None:
            query = query.where(repository_entries_table.c.project_id == project_id)
        if status is not None:
            query = query.where(repository_entries_table.c.status == status)
        elif not include_retired:
            query = query.where(repository_entries_table.c.status != "retired")
        if entry_type is not None:
            query = query.where(repository_entries_table.c.type == entry_type)
        with self.engine.connect() as connection:
            rows = connection.execute(query).mappings().all()
        return [_row_to_repository_entry(dict(row)) for row in rows]

    def transition_repository_entry_status(
        self,
        entry_id: str,
        status: str,
        *,
        updated_at: datetime,
        published_version: int | None = None,
    ) -> RepositoryEntryRecord:
        existing = self.get_repository_entry(entry_id)
        if existing is None:
            raise ValueError(f"repository entry not found: {entry_id}")
        _validate_repository_transition(existing.status, status)
        values: dict[str, Any] = {"status": status, "updated_at": updated_at}
        if status == "published":
            version = published_version or existing.published_version
            if version is None:
                raise ValueError("published repository entries require a published version")
            with self.engine.begin() as connection:
                _require_repository_version_status(connection, entry_id, version, "published")
                values["published_version"] = version
                connection.execute(
                    update(repository_entries_table)
                    .where(repository_entries_table.c.id == entry_id)
                    .values(**values)
                )
        else:
            if published_version is not None:
                values["published_version"] = published_version
            with self.engine.begin() as connection:
                connection.execute(
                    update(repository_entries_table)
                    .where(repository_entries_table.c.id == entry_id)
                    .values(**values)
                )
        loaded = self.get_repository_entry(entry_id)
        assert loaded is not None
        return loaded

    def delete_repository_entry(self, entry_id: str) -> dict[str, Any]:
        """Permanently delete a non-published repository entry and generated bundles."""
        with self.engine.begin() as connection:
            entry_row = (
                connection.execute(
                    select(repository_entries_table).where(
                        repository_entries_table.c.id == entry_id
                    )
                )
                .mappings()
                .one_or_none()
            )
            if entry_row is None:
                raise ValueError(f"repository entry not found: {entry_id}")
            entry = _row_to_repository_entry(dict(entry_row))
            if entry.status not in {"draft", "rejected", "retired"}:
                raise ValueError(
                    "repository entries must be draft, rejected, or retired before deletion"
                )

            version_rows = (
                connection.execute(
                    select(repository_versions_table.c.kind).where(
                        repository_versions_table.c.entry_id == entry_id
                    )
                )
                .mappings()
                .all()
            )
            version_kinds = [str(row["kind"]) for row in version_rows]

            if entry.type == "notebook_service" and version_kinds:
                active_service = (
                    connection.execute(
                        select(
                            notebook_services_table.c.kind,
                            notebook_services_table.c.runtime_status,
                            notebook_services_table.c.active_service_id,
                        )
                        .where(notebook_services_table.c.kind.in_(version_kinds))
                        .where(notebook_services_table.c.active_service_id.is_not(None))
                    )
                    .mappings()
                    .first()
                )
                if active_service is not None:
                    raise ValueError(
                        "repository entry has an active service runtime; stop it before deleting"
                    )

            deleted_notebook_records = 0
            if version_kinds:
                if entry.type == "notebook_function":
                    result = connection.execute(
                        delete(notebook_goblins_table).where(
                            notebook_goblins_table.c.kind.in_(version_kinds)
                        )
                    )
                else:
                    result = connection.execute(
                        delete(notebook_services_table).where(
                            notebook_services_table.c.kind.in_(version_kinds)
                        )
                    )
                deleted_notebook_records = int(result.rowcount or 0)

            version_result = connection.execute(
                delete(repository_versions_table).where(
                    repository_versions_table.c.entry_id == entry_id
                )
            )
            connection.execute(
                delete(repository_entries_table).where(repository_entries_table.c.id == entry_id)
            )

        return {
            "entry_id": entry.id,
            "name": entry.name,
            "status": entry.status,
            "deleted_versions": int(version_result.rowcount or 0),
            "deleted_notebook_records": deleted_notebook_records,
        }

    def create_repository_version(
        self,
        version: RepositoryVersionRecord,
    ) -> RepositoryVersionRecord:
        if version.status != "draft" or version.approval_status != "draft":
            raise ValueError("repository versions must start as drafts")
        if version.approved_by is not None or version.approved_at is not None:
            raise ValueError("draft repository versions cannot keep approval")
        if version.published_at is not None:
            raise ValueError("draft repository versions cannot be published")
        with self.engine.begin() as connection:
            entry = (
                connection.execute(
                    select(repository_entries_table).where(
                        repository_entries_table.c.id == version.entry_id
                    )
                )
                .mappings()
                .one_or_none()
            )
            if entry is None:
                raise ValueError(f"repository entry not found: {version.entry_id}")
            if entry["status"] == "retired":
                raise ValueError("retired repository entries cannot receive new versions")
            latest = (
                connection.execute(
                    select(repository_versions_table)
                    .where(repository_versions_table.c.entry_id == version.entry_id)
                    .order_by(repository_versions_table.c.version.desc())
                )
                .mappings()
                .first()
            )
            if latest is None and version.version != 1:
                raise ValueError("first repository version must be 1")
            if latest is not None:
                if version.version != latest["version"] + 1:
                    raise ValueError(
                        "repository source changes must create the next sequential version"
                    )
                if version.source_hash == latest["source_hash"]:
                    raise ValueError("repository version source hash must change")
            duplicate = (
                connection.execute(
                    select(repository_versions_table.c.id)
                    .where(repository_versions_table.c.entry_id == version.entry_id)
                    .where(repository_versions_table.c.version == version.version)
                )
                .mappings()
                .one_or_none()
            )
            if duplicate is not None:
                raise ValueError("repository version already exists")
            connection.execute(
                repository_versions_table.insert().values(
                    **_repository_version_values(version)
                )
            )
            connection.execute(
                update(repository_entries_table)
                .where(repository_entries_table.c.id == version.entry_id)
                .values(status="draft", updated_at=version.updated_at)
            )
        loaded = self.get_repository_version(version.entry_id, version.version)
        assert loaded is not None
        return loaded

    def update_repository_version(
        self,
        version: RepositoryVersionRecord,
    ) -> RepositoryVersionRecord:
        with self.engine.connect() as connection:
            row = (
                connection.execute(
                    select(repository_versions_table).where(
                        repository_versions_table.c.id == version.id
                    )
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise ValueError(f"repository version not found: {version.id}")
        existing = _row_to_repository_version(dict(row))
        if existing.status == "published":
            raise ValueError("published repository versions are immutable")
        for field in REPOSITORY_VERSION_IDENTITY_FIELDS:
            if getattr(existing, field) != getattr(version, field):
                raise ValueError("repository version identity cannot change")
        _validate_repository_transition(existing.status, version.status)
        if existing.source_hash != version.source_hash:
            if existing.status != "draft":
                raise ValueError("source changes must create a new draft version")
            if (
                version.status != "draft"
                or version.approval_status != "draft"
                or version.validation_proof
                or version.approved_by is not None
                or version.approved_at is not None
                or version.published_at is not None
            ):
                raise ValueError("source changes must clear review state")
        with self.engine.begin() as connection:
            if version.status == "published":
                _ensure_repository_version_publishable(version)
            connection.execute(
                update(repository_versions_table)
                .where(repository_versions_table.c.id == version.id)
                .values(**_repository_version_values(version))
            )
            if version.status == "published":
                connection.execute(
                    update(repository_entries_table)
                    .where(repository_entries_table.c.id == version.entry_id)
                    .values(
                        status="published",
                        published_version=version.version,
                        updated_at=version.updated_at,
                    )
                )
        loaded = self.get_repository_version(version.entry_id, version.version)
        assert loaded is not None
        return loaded

    def get_repository_version(
        self,
        entry_id: str,
        version: int,
    ) -> RepositoryVersionRecord | None:
        with self.engine.connect() as connection:
            row = (
                connection.execute(
                    select(repository_versions_table)
                    .where(repository_versions_table.c.entry_id == entry_id)
                    .where(repository_versions_table.c.version == version)
                )
                .mappings()
                .one_or_none()
            )
        return _row_to_repository_version(dict(row)) if row else None

    def get_repository_version_by_project_name(
        self,
        project_id: str | None,
        name: str,
        version: int,
        *,
        include_retired: bool = False,
    ) -> RepositoryVersionRecord | None:
        entry = self.get_repository_entry_by_project_name(
            project_id,
            name,
            include_retired=include_retired,
        )
        if entry is None:
            return None
        return self.get_repository_version(entry.id, version)

    def list_repository_versions(
        self,
        entry_id: str,
        *,
        status: str | None = None,
    ) -> list[RepositoryVersionRecord]:
        query = (
            select(repository_versions_table)
            .where(repository_versions_table.c.entry_id == entry_id)
            .order_by(repository_versions_table.c.version)
        )
        if status is not None:
            query = query.where(repository_versions_table.c.status == status)
        with self.engine.connect() as connection:
            rows = connection.execute(query).mappings().all()
        return [_row_to_repository_version(dict(row)) for row in rows]

    def transition_repository_version_status(
        self,
        entry_id: str,
        version: int,
        status: str,
        *,
        updated_at: datetime,
        validation_proof: dict[str, Any] | None = None,
        approved_by: str | None = None,
        approved_at: datetime | None = None,
        published_at: datetime | None = None,
    ) -> RepositoryVersionRecord:
        existing = self.get_repository_version(entry_id, version)
        if existing is None:
            raise ValueError(f"repository version not found: {entry_id}:{version}")
        if existing.status == "published":
            if status == "published":
                return existing
            raise ValueError("published repository versions are immutable")
        _validate_repository_transition(existing.status, status)
        values: dict[str, Any] = {"status": status, "updated_at": updated_at}
        if validation_proof is not None:
            values["validation_proof_json"] = json.dumps(validation_proof)
        if status in {"draft", "rejected", "retired"}:
            values["approval_status"] = status
            values["approved_by"] = None
            values["approved_at"] = None
            if status == "draft":
                values["validation_proof_json"] = "{}"
                values["published_at"] = None
        elif status == "validated":
            proof = validation_proof if validation_proof is not None else existing.validation_proof
            if not proof:
                raise ValueError("validated repository versions require validation proof")
            values["approval_status"] = "validated"
            values["validation_proof_json"] = json.dumps(proof)
        elif status == "pending_review":
            values["approval_status"] = "pending_review"
        elif status == "approved":
            approver = approved_by or existing.approved_by
            approved_time = approved_at or existing.approved_at or updated_at
            if not approver:
                raise ValueError("approved repository versions require an approver")
            values["approval_status"] = "approved"
            values["approved_by"] = approver
            values["approved_at"] = approved_time
        elif status == "published":
            approver = approved_by or existing.approved_by
            approved_time = approved_at or existing.approved_at
            if not approver or approved_time is None:
                raise ValueError("published repository versions require approval")
            values["approval_status"] = "published"
            values["approved_by"] = approver
            values["approved_at"] = approved_time
            values["published_at"] = published_at or updated_at
        with self.engine.begin() as connection:
            connection.execute(
                update(repository_versions_table)
                .where(repository_versions_table.c.entry_id == entry_id)
                .where(repository_versions_table.c.version == version)
                .values(**values)
            )
            entry_values: dict[str, Any] = {
                "status": status,
                "updated_at": updated_at,
            }
            if status == "published":
                entry_values["published_version"] = version
            connection.execute(
                update(repository_entries_table)
                .where(repository_entries_table.c.id == entry_id)
                .values(**entry_values)
            )
        loaded = self.get_repository_version(entry_id, version)
        assert loaded is not None
        return loaded

    def get_latest_worker_validation(
        self,
        *,
        kind: str,
        image_digest: str,
        contract_version: str,
        validator_version: str,
    ) -> WorkerValidationRecord | None:
        """Return the newest validation record for one resolved image identity."""
        with self.engine.begin() as connection:
            row = connection.execute(
                select(worker_validations_table)
                .where(worker_validations_table.c.kind == kind)
                .where(worker_validations_table.c.image_digest == image_digest)
                .where(worker_validations_table.c.contract_version == contract_version)
                .where(worker_validations_table.c.validator_version == validator_version)
                .order_by(worker_validations_table.c.validated_at.desc())
            ).first()
        return _row_to_worker_validation(dict(row._mapping)) if row else None

    def list_worker_validations(
        self,
        *,
        kind: str | None = None,
        limit: int = 100,
    ) -> list[WorkerValidationRecord]:
        """Return recent worker validation records for API/CLI/admin status surfaces."""
        query = select(worker_validations_table).order_by(
            worker_validations_table.c.validated_at.desc()
        )
        if kind is not None:
            query = query.where(worker_validations_table.c.kind == kind)
        with self.engine.begin() as connection:
            rows = connection.execute(query.limit(limit)).fetchall()
        return [_row_to_worker_validation(dict(row._mapping)) for row in rows]

    def latest_worker_validation_for_kind(
        self,
        kind: str,
    ) -> WorkerValidationRecord | None:
        """Return the newest validation record for one goblin kind."""
        with self.engine.begin() as connection:
            row = connection.execute(
                select(worker_validations_table)
                .where(worker_validations_table.c.kind == kind)
                .order_by(worker_validations_table.c.validated_at.desc())
            ).first()
        return _row_to_worker_validation(dict(row._mapping)) if row else None

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
                    probe_path=service.probe_path,
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

    def update_long_service_status(
        self,
        service_id: str,
        *,
        status: str,
        last_probe_json: dict[str, Any] | None = None,
    ) -> LongServiceRecord | None:
        """Update the lifecycle status for a registered long-running service goblin."""
        values: dict[str, Any] = {"status": status}
        if last_probe_json is not None:
            values["last_probe_json"] = json.dumps(last_probe_json)
        with self.engine.begin() as connection:
            connection.execute(
                update(long_services_table)
                .where(long_services_table.c.id == service_id)
                .values(**values)
            )
        return self.get_long_service(service_id)

    def save_image_promotion(self, promotion: ImagePromotionRecord) -> None:
        """Insert one worker image promotion proof record."""
        with self.engine.begin() as connection:
            connection.execute(
                image_promotions_table.insert().values(
                    id=promotion.id,
                    kind=promotion.kind,
                    source_image=promotion.source_image,
                    target_image=promotion.target_image,
                    status=promotion.status,
                    actor=promotion.actor,
                    digest=promotion.digest,
                    created_at=promotion.created_at,
                    updated_at=promotion.updated_at,
                    detail_json=json.dumps(promotion.detail),
                )
            )

    def get_image_promotion(self, promotion_id: str) -> ImagePromotionRecord | None:
        """Load one image promotion record by ID."""
        with self.engine.connect() as connection:
            row = (
                connection.execute(
                    select(image_promotions_table).where(
                        image_promotions_table.c.id == promotion_id
                    )
                )
                .mappings()
                .one_or_none()
            )
        return _row_to_image_promotion(dict(row)) if row else None

    def list_image_promotions(self, *, limit: int = 100) -> list[ImagePromotionRecord]:
        """Return recent worker image promotion records."""
        with self.engine.connect() as connection:
            rows = (
                connection.execute(
                    select(image_promotions_table)
                    .order_by(image_promotions_table.c.created_at.desc())
                    .limit(max(1, min(limit, 500)))
                )
                .mappings()
                .all()
            )
        return [_row_to_image_promotion(dict(row)) for row in rows]

    def update_image_promotion(
        self,
        promotion_id: str,
        *,
        status: str,
        updated_at: datetime,
        digest: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> ImagePromotionRecord | None:
        """Update image promotion status, digest, and operator proof details."""
        values: dict[str, Any] = {"status": status, "updated_at": updated_at}
        if digest is not None:
            values["digest"] = digest
        if detail is not None:
            values["detail_json"] = json.dumps(detail)
        with self.engine.begin() as connection:
            connection.execute(
                update(image_promotions_table)
                .where(image_promotions_table.c.id == promotion_id)
                .values(**values)
            )
        return self.get_image_promotion(promotion_id)

    def save_deployment_record(self, record: DeploymentRecord) -> None:
        """Insert one deployment orchestration proof record."""
        with self.engine.begin() as connection:
            connection.execute(
                deployment_records_table.insert().values(
                    id=record.id,
                    name=record.name,
                    action=record.action,
                    status=record.status,
                    actor=record.actor,
                    command_json=json.dumps(record.command),
                    output=record.output,
                    created_at=record.created_at,
                    updated_at=record.updated_at,
                    detail_json=json.dumps(record.detail),
                )
            )

    def get_deployment_record(self, record_id: str) -> DeploymentRecord | None:
        """Load one deployment orchestration record by ID."""
        with self.engine.connect() as connection:
            row = (
                connection.execute(
                    select(deployment_records_table).where(
                        deployment_records_table.c.id == record_id
                    )
                )
                .mappings()
                .one_or_none()
            )
        return _row_to_deployment_record(dict(row)) if row else None

    def list_deployment_records(self, *, limit: int = 100) -> list[DeploymentRecord]:
        """Return recent deployment orchestration records."""
        with self.engine.connect() as connection:
            rows = (
                connection.execute(
                    select(deployment_records_table)
                    .order_by(deployment_records_table.c.created_at.desc())
                    .limit(max(1, min(limit, 500)))
                )
                .mappings()
                .all()
            )
        return [_row_to_deployment_record(dict(row)) for row in rows]

    def cleanup_runtime_rows(
        self,
        *,
        project_id: str | None = None,
        dry_run: bool = True,
        include_unprobed_services: bool = True,
    ) -> dict[str, int]:
        """Delete historical runtime rows while preserving active work and auth state."""
        terminal_statuses = ["completed", "failed", "timed_out", "cancelled"]
        with self.engine.begin() as connection:
            terminal_jobs_query = select(jobs_table.c.id).where(
                jobs_table.c.status.in_(terminal_statuses)
            )
            if project_id is not None:
                terminal_jobs_query = terminal_jobs_query.where(
                    jobs_table.c.project_id == project_id
                )
            terminal_job_ids = [
                row[0] for row in connection.execute(terminal_jobs_query).all()
            ]

            run_query = select(runs_table.c.id).where(runs_table.c.status.in_(terminal_statuses))
            if terminal_job_ids:
                run_query = run_query.where(runs_table.c.job_id.in_(terminal_job_ids))
            elif project_id is not None:
                run_query = run_query.where(runs_table.c.project_id == project_id)
            run_ids = [row[0] for row in connection.execute(run_query).all()]

            fanout_query = select(fanouts_table.c.id)
            if project_id is not None:
                fanout_query = fanout_query.where(fanouts_table.c.project_id == project_id)
            candidate_fanout_ids = [
                row[0] for row in connection.execute(fanout_query).all()
            ]
            fanout_ids: list[str] = []
            for fanout_id in candidate_fanout_ids:
                active_child = connection.execute(
                    select(jobs_table.c.id)
                    .where(jobs_table.c.fanout_id == fanout_id)
                    .where(jobs_table.c.status.not_in(terminal_statuses))
                    .limit(1)
                ).first()
                if active_child is None:
                    fanout_ids.append(fanout_id)

            service_ids = [
                row[0]
                for row in connection.execute(
                    select(long_services_table.c.id).where(
                        long_services_table.c.status.in_(["failed", "stopped"])
                    )
                ).all()
            ]
            if include_unprobed_services:
                service_ids.extend(
                    row[0]
                    for row in connection.execute(
                        select(long_services_table.c.id)
                        .where(long_services_table.c.status == "registered")
                        .where(long_services_table.c.last_probe_at.is_(None))
                    ).all()
                )
            service_ids = list(dict.fromkeys(service_ids))
            if project_id is not None:
                scoped_service_ids = {
                    row[0]
                    for row in connection.execute(
                        select(long_services_table.c.id).where(
                            long_services_table.c.project_id == project_id
                        )
                    ).all()
                }
                service_ids = [
                    service_id
                    for service_id in service_ids
                    if service_id in scoped_service_ids
                ]

            event_query = select(events_table.c.id)
            if project_id is not None:
                event_query = event_query.where(events_table.c.project_id == project_id)
            event_ids = [row[0] for row in connection.execute(event_query).all()]

            worker_heartbeat_ids = [
                row[0]
                for row in connection.execute(
                    select(heartbeats_table.c.owner_id).where(
                        heartbeats_table.c.owner_type == "worker"
                    )
                ).all()
            ]

            counts = {
                "artifacts": 0,
                "handoffs": 0,
                "runs": len(run_ids),
                "jobs": len(terminal_job_ids),
                "fanouts": len(fanout_ids),
                "events": len(event_ids),
                "worker_heartbeats": len(worker_heartbeat_ids),
                "long_services": len(service_ids),
            }
            if run_ids:
                counts["artifacts"] = len(
                    connection.execute(
                        select(artifacts_table.c.id).where(artifacts_table.c.run_id.in_(run_ids))
                    ).all()
                )
                counts["handoffs"] = len(
                    connection.execute(
                        select(handoffs_table.c.id).where(handoffs_table.c.run_id.in_(run_ids))
                    ).all()
                )
            if dry_run:
                return counts

            if run_ids:
                connection.execute(delete(artifacts_table).where(artifacts_table.c.run_id.in_(run_ids)))
                connection.execute(delete(handoffs_table).where(handoffs_table.c.run_id.in_(run_ids)))
                connection.execute(delete(runs_table).where(runs_table.c.id.in_(run_ids)))
            if terminal_job_ids:
                connection.execute(delete(jobs_table).where(jobs_table.c.id.in_(terminal_job_ids)))
            if fanout_ids:
                connection.execute(delete(fanouts_table).where(fanouts_table.c.id.in_(fanout_ids)))
            if event_ids:
                connection.execute(delete(events_table).where(events_table.c.id.in_(event_ids)))
            if worker_heartbeat_ids:
                connection.execute(
                    delete(heartbeats_table).where(
                        heartbeats_table.c.owner_id.in_(worker_heartbeat_ids)
                    )
                )
            if service_ids:
                connection.execute(
                    delete(long_services_table).where(long_services_table.c.id.in_(service_ids))
                )
            return counts

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
                    resource_policy_json=(
                        json.dumps(run.resource_policy) if run.resource_policy else None
                    ),
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
            query = select(jobs_table).order_by(jobs_table.c.created_at.desc())
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
                    jobs_table.c.status.in_(["leased", "running"])
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

    def count_active_jobs(self, kind: str, *, exclude_job_id: str | None = None) -> int:
        """Count leased/running jobs for one goblin kind."""
        with self.engine.connect() as connection:
            query = select(func.count()).select_from(jobs_table).where(
                jobs_table.c.kind == kind,
                jobs_table.c.status.in_(["leased", "running"]),
            )
            if exclude_job_id is not None:
                query = query.where(jobs_table.c.id != exclude_job_id)
            return int(connection.execute(query).scalar_one())

    def count_active_project_jobs(
        self,
        project_id: str | None,
        *,
        exclude_job_id: str | None = None,
    ) -> int:
        """Count leased/running jobs in one project scope."""
        with self.engine.connect() as connection:
            query = select(func.count()).select_from(jobs_table).where(
                jobs_table.c.status.in_(["leased", "running"]),
            )
            if project_id is None:
                query = query.where(jobs_table.c.project_id.is_(None))
            else:
                query = query.where(jobs_table.c.project_id == project_id)
            if exclude_job_id is not None:
                query = query.where(jobs_table.c.id != exclude_job_id)
            return int(connection.execute(query).scalar_one())

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

    def list_runs_page(
        self,
        *,
        project_id: str | None = None,
        status: str | None = None,
        kind: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[RunRecord]:
        """Return a bounded page of runs for API and admin UI inspection."""
        with self.engine.connect() as connection:
            query = select(runs_table).order_by(runs_table.c.started_at.desc())
            if project_id is not None:
                query = query.where(runs_table.c.project_id == project_id)
            if status is not None:
                query = query.where(runs_table.c.status == status)
            if kind is not None:
                query = query.where(runs_table.c.kind == kind)
            rows = (
                connection.execute(query.offset(max(offset, 0)).limit(max(1, min(limit, 500))))
                .mappings()
                .all()
            )
        return [_row_to_run(dict(row)) for row in rows]

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

    def list_artifacts_for_project(self, project_id: str | None = None) -> list[ArtifactRecord]:
        """Return artifact metadata rows, optionally scoped by owning run project."""
        query = select(artifacts_table)
        if project_id is not None:
            query = query.join(runs_table, artifacts_table.c.run_id == runs_table.c.id).where(
                runs_table.c.project_id == project_id
            )
        with self.engine.connect() as connection:
            rows = connection.execute(query.order_by(artifacts_table.c.name)).mappings().all()
        return [
            ArtifactRecord(
                name=row["name"],
                uri=row["uri"],
                media_type=row["media_type"],
            )
            for row in rows
        ]


def _repository_entry_values(record: RepositoryEntryRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "name": record.name,
        "kind": record.kind,
        "type": record.type,
        "project_id": record.project_id,
        "owner": record.owner,
        "display_name": record.display_name,
        "description": record.description,
        "tags_json": json.dumps(record.tags),
        "status": record.status,
        "published_version": record.published_version,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


def _repository_version_values(record: RepositoryVersionRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "entry_id": record.entry_id,
        "version": record.version,
        "kind": record.kind,
        "source_hash": record.source_hash,
        "runner_image": record.runner_image,
        "validation_proof_json": json.dumps(record.validation_proof),
        "approval_status": record.approval_status,
        "status": record.status,
        "approved_by": record.approved_by,
        "approved_at": record.approved_at,
        "published_at": record.published_at,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


def _ensure_repository_entry_name_available(
    connection: Any,
    project_id: str | None,
    name: str,
    *,
    exclude_entry_id: str | None = None,
) -> None:
    query = select(repository_entries_table.c.id).where(
        repository_entries_table.c.name == name,
        repository_entries_table.c.status != "retired",
    )
    if project_id is None:
        query = query.where(repository_entries_table.c.project_id.is_(None))
    else:
        query = query.where(repository_entries_table.c.project_id == project_id)
    if exclude_entry_id is not None:
        query = query.where(repository_entries_table.c.id != exclude_entry_id)
    existing = connection.execute(query.limit(1)).scalar_one_or_none()
    if existing is not None:
        raise ValueError("repository entry name already exists in project")


def _validate_repository_transition(current: str, target: str) -> None:
    if current == target:
        return
    allowed = REPOSITORY_STATUS_TRANSITIONS.get(current)
    if allowed is None or target not in allowed:
        raise ValueError(f"cannot transition repository record from {current} to {target}")


def _require_repository_version_status(
    connection: Any,
    entry_id: str,
    version: int,
    status: str,
) -> None:
    existing = (
        connection.execute(
            select(repository_versions_table.c.status)
            .where(repository_versions_table.c.entry_id == entry_id)
            .where(repository_versions_table.c.version == version)
        )
        .scalars()
        .one_or_none()
    )
    if existing != status:
        raise ValueError(f"repository version {version} is not {status}")


def _ensure_repository_version_publishable(record: RepositoryVersionRecord) -> None:
    if record.status != "published":
        return
    if record.approval_status not in {"approved", "published"}:
        raise ValueError("published repository versions require approval")
    if record.approved_by is None or record.approved_at is None:
        raise ValueError("published repository versions require approval")
    if record.published_at is None:
        raise ValueError("published repository versions require a publication timestamp")



__all__ = [
    "ArtifactRecord",
    "ApiTokenRecord",
    "AuditLogRecord",
    "DEFAULT_DB_PATH",
    "DeploymentRecord",
    "EventRecord",
    "FanoutRecord",
    "HeartbeatRecord",
    "HandoffRecord",
    "ImagePromotionRecord",
    "LongServiceRecord",
    "NotebookServiceRecord",
    "RepositoryEntryRecord",
    "RepositoryVersionRecord",
    "SQLiteStore",
]
