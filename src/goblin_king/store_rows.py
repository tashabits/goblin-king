"""Row-mapping helpers for Goblin King SQLite persistence."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from goblin_king.contracts import (
    ApiTokenRecord,
    AuditLogRecord,
    DeploymentRecord,
    EventRecord,
    FanoutRecord,
    GoblinResult,
    HeartbeatRecord,
    ImagePromotionRecord,
    JobRecord,
    LongServiceRecord,
    NotebookGoblinRecord,
    NotebookServiceRecord,
    ProjectRecord,
    RunRecord,
    ScheduleRecord,
    UserRecord,
    WorkerValidationRecord,
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
        resource_policy=(
            json.loads(payload["resource_policy_json"])
            if payload.get("resource_policy_json")
            else None
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
        probe_path=payload.get("probe_path") or "/hello",
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


def _row_to_notebook_goblin(payload: dict[str, Any]) -> NotebookGoblinRecord:
    """Convert a SQLAlchemy row mapping into a NotebookGoblinRecord."""
    return NotebookGoblinRecord(
        kind=payload["kind"],
        project_id=payload.get("project_id"),
        display_name=payload["display_name"],
        image=payload["image"],
        source=payload["source"],
        source_hash=payload["source_hash"],
        function_name=payload["function_name"],
        timeout_seconds=payload.get("timeout_seconds"),
        max_retries=payload.get("max_retries") or 0,
        created_at=_coerce_datetime(payload["created_at"]),
        updated_at=_coerce_datetime(payload["updated_at"]),
        created_by=payload["created_by"],
        metadata=json.loads(payload.get("metadata_json") or "{}"),
    )


def _row_to_notebook_service(payload: dict[str, Any]) -> NotebookServiceRecord:
    """Convert a SQLAlchemy row mapping into a NotebookServiceRecord."""
    return NotebookServiceRecord(
        kind=payload["kind"],
        project_id=payload.get("project_id"),
        display_name=payload["display_name"],
        image=payload["image"],
        source=payload["source"],
        source_hash=payload["source_hash"],
        app_name=payload["app_name"],
        requirements=json.loads(payload.get("requirements_json") or "[]"),
        port=payload.get("port") or 8080,
        probe_path=payload.get("probe_path") or "/hello",
        created_at=_coerce_datetime(payload["created_at"]),
        updated_at=_coerce_datetime(payload["updated_at"]),
        created_by=payload["created_by"],
        metadata=json.loads(payload.get("metadata_json") or "{}"),
        runtime_backend=payload.get("runtime_backend"),
        runtime_name=payload.get("runtime_name"),
        runtime_status=payload.get("runtime_status") or "declared",
        active_service_id=payload.get("active_service_id"),
    )


def _row_to_image_promotion(payload: dict[str, Any]) -> ImagePromotionRecord:
    """Convert a SQLAlchemy row mapping into an ImagePromotionRecord."""
    return ImagePromotionRecord(
        id=payload["id"],
        kind=payload["kind"],
        source_image=payload["source_image"],
        target_image=payload["target_image"],
        status=payload["status"],
        actor=payload["actor"],
        digest=payload.get("digest"),
        created_at=_coerce_datetime(payload["created_at"]),
        updated_at=_coerce_datetime(payload["updated_at"]),
        detail=json.loads(payload.get("detail_json") or "{}"),
    )


def _row_to_deployment_record(payload: dict[str, Any]) -> DeploymentRecord:
    """Convert a SQLAlchemy row mapping into a DeploymentRecord."""
    return DeploymentRecord(
        id=payload["id"],
        name=payload["name"],
        action=payload["action"],
        status=payload["status"],
        actor=payload["actor"],
        command=json.loads(payload.get("command_json") or "[]"),
        output=payload.get("output"),
        created_at=_coerce_datetime(payload["created_at"]),
        updated_at=_coerce_datetime(payload["updated_at"]),
        detail=json.loads(payload.get("detail_json") or "{}"),
    )


def _row_to_worker_validation(payload: dict[str, Any]) -> WorkerValidationRecord:
    """Convert a SQLAlchemy row mapping into a WorkerValidationRecord."""
    return WorkerValidationRecord(
        id=payload["id"],
        kind=payload["kind"],
        image=payload["image"],
        image_digest=payload["image_digest"],
        contract_version=payload["contract_version"],
        validator_version=payload["validator_version"],
        validated_at=_coerce_datetime(payload["validated_at"]),
        status=payload["status"],
        failure_reasons=json.loads(payload.get("failure_reasons_json") or "[]"),
        effective_policy=json.loads(payload.get("effective_policy_json") or "{}"),
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
