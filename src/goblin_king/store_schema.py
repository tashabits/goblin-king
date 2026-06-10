"""SQLAlchemy table definitions for Goblin King SQLite persistence."""

from __future__ import annotations

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
)

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
    Column("resource_policy_json", Text, nullable=True),
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

image_promotions_table = Table(
    "image_promotions",
    metadata,
    Column("id", String, primary_key=True),
    Column("kind", String, nullable=False),
    Column("source_image", Text, nullable=False),
    Column("target_image", Text, nullable=False),
    Column("status", String, nullable=False),
    Column("actor", String, nullable=False),
    Column("digest", Text, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("detail_json", Text, nullable=False, default="{}"),
)

deployment_records_table = Table(
    "deployment_records",
    metadata,
    Column("id", String, primary_key=True),
    Column("name", String, nullable=False),
    Column("action", String, nullable=False),
    Column("status", String, nullable=False),
    Column("actor", String, nullable=False),
    Column("command_json", Text, nullable=False, default="[]"),
    Column("output", Text, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("detail_json", Text, nullable=False, default="{}"),
)

worker_validations_table = Table(
    "worker_validations",
    metadata,
    Column("id", String, primary_key=True),
    Column("kind", String, nullable=False),
    Column("image", Text, nullable=False),
    Column("image_digest", Text, nullable=False),
    Column("contract_version", String, nullable=False),
    Column("validator_version", String, nullable=False),
    Column("validated_at", DateTime(timezone=True), nullable=False),
    Column("status", String, nullable=False),
    Column("failure_reasons_json", Text, nullable=False, default="[]"),
    Column("effective_policy_json", Text, nullable=False, default="{}"),
)
