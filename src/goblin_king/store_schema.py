"""SQLAlchemy table definitions for Goblin King SQLite persistence."""

from __future__ import annotations

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
)

metadata = MetaData()

pipeline_build_requests_table = Table(
    "pipeline_build_requests",
    metadata,
    Column("sequence", Integer, primary_key=True, autoincrement=True),
    Column("build_id", String, nullable=False, unique=True),
    Column("request_key", String, nullable=False, unique=True),
    Column("project_id", String, nullable=False, index=True),
    Column("snapshot_json", Text, nullable=False),
    sqlite_autoincrement=True,
)

pipeline_receipts_table = Table(
    "pipeline_receipts",
    metadata,
    Column("receipt_key", String, primary_key=True),
    Column("payload_hash", String, nullable=False),
    Column("request_key", String, nullable=False),
    Column("build_id", String, ForeignKey("pipeline_build_requests.build_id"), nullable=False),
)

pipeline_registrations_table = Table(
    "pipeline_registrations",
    metadata,
    Column("key", String, primary_key=True),
    Column("project_id", String, nullable=False, index=True),
    Column("version", Integer, nullable=False),
    Column("enabled", Integer, nullable=False, default=0),
    Column("document", Text, nullable=False),
)

pipeline_reservations_table = Table(
    "pipeline_reservations",
    metadata,
    Column("destination", String, primary_key=True),
    Column("registration_key", String, nullable=False, index=True),
)

pipeline_environment_states_table = Table(
    "pipeline_environment_states",
    metadata,
    Column("key", String, primary_key=True),
    Column("registration_key", String, nullable=False, index=True),
    Column("project_id", String, nullable=False, index=True),
    Column("version", Integer, nullable=False),
    Column("document", Text, nullable=False),
)

pipeline_dispatch_table = Table(
    "pipeline_dispatch",
    metadata,
    Column("id", String, primary_key=True),
    Column("build_id", String, nullable=False, index=True),
    Column("job_id", String, nullable=False, unique=True),
    Column("version", Integer, nullable=False),
    Column("status", String, nullable=False, index=True),
    Column("document", Text, nullable=False),
)

pipeline_builds_table = Table(
    "pipeline_builds",
    metadata,
    Column("id", String, primary_key=True),
    Column("project_id", String, nullable=False, index=True),
    Column("version", Integer, nullable=False),
    Column("status", String, nullable=False, index=True),
    Column("document", Text, nullable=False),
)

pipeline_operations_table = Table(
    "pipeline_operations",
    metadata,
    Column("id", String, primary_key=True),
    Column("project_id", String, nullable=False, index=True),
    Column("release_key", String, nullable=False, index=True),
    Column("version", Integer, nullable=False),
    Column("status", String, nullable=False, index=True),
    Column("document", Text, nullable=False),
)

pipeline_journal_table = Table(
    "pipeline_journal",
    metadata,
    Column("sequence", Integer, primary_key=True, autoincrement=True),
    Column("project_id", String, nullable=False, index=True),
    Column("entity", String, nullable=False),
    Column("action", String, nullable=False),
    Column("at", String, nullable=False),
    Column("evidence", Text, nullable=False),
    sqlite_autoincrement=True,
)

pipeline_actions_table = Table(
    "pipeline_actions",
    metadata,
    Column("key", String, primary_key=True),
    Column("fingerprint", String, nullable=False),
    Column("response", Text, nullable=False),
)

pipeline_provider_cursors_table = Table(
    "pipeline_provider_cursors",
    metadata,
    Column("key", String, primary_key=True),
    Column("version", Integer, nullable=False),
    Column("document", Text, nullable=False),
)

job_submissions_table = Table(
    "job_submissions",
    metadata,
    Column("job_id", String, primary_key=True),
    Column("fingerprint", Text, nullable=False),
)

pipeline_notifications_table = Table(
    "pipeline_notifications",
    metadata,
    Column("id", String, primary_key=True),
    Column("status", String, nullable=False, index=True),
    Column("attempts", Integer, nullable=False, default=0),
    Column("not_before", Float, nullable=False, default=0),
    Column("document", Text, nullable=False),
)

pipeline_webhooks_table = Table(
    "pipeline_webhooks",
    metadata,
    Column("id", String, primary_key=True),
    Column("fingerprint", String, nullable=False),
    Column("status", String, nullable=False, index=True),
    Column("document", Text, nullable=False),
)

causal_sequences_table = Table(
    "causal_sequences",
    metadata,
    Column("scope", String, primary_key=True),
    Column("value", Integer, nullable=False),
)

event_stream_deliveries_table = Table(
    "event_stream_deliveries",
    metadata,
    Column("target", String, primary_key=True),
    Column("delivered_sequence", Integer, nullable=False, default=0),
    Column("stream_id_offset", Integer, nullable=True),
)

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
    Column("sequence", Integer, nullable=False),
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
    Column("probe_path", Text, nullable=False, default="/hello"),
    Column("status", String, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("created_by", String, nullable=False),
    Column("last_probe_at", DateTime(timezone=True), nullable=True),
    Column("last_probe_json", Text, nullable=True),
)

notebook_goblins_table = Table(
    "notebook_goblins",
    metadata,
    Column("kind", String, primary_key=True),
    Column("project_id", String, nullable=True),
    Column("display_name", Text, nullable=False),
    Column("image", Text, nullable=False),
    Column("source", Text, nullable=False),
    Column("source_hash", String, nullable=False),
    Column("function_name", String, nullable=False, default="run"),
    Column("timeout_seconds", Integer, nullable=True),
    Column("max_retries", Integer, nullable=False, default=0),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("created_by", String, nullable=False),
    Column("metadata_json", Text, nullable=False, default="{}"),
)

notebook_services_table = Table(
    "notebook_services",
    metadata,
    Column("kind", String, primary_key=True),
    Column("project_id", String, nullable=True),
    Column("display_name", Text, nullable=False),
    Column("image", Text, nullable=False),
    Column("source", Text, nullable=False),
    Column("source_hash", String, nullable=False),
    Column("app_name", String, nullable=False, default="app"),
    Column("requirements_json", Text, nullable=False, default="[]"),
    Column("port", Integer, nullable=False, default=8080),
    Column("probe_path", Text, nullable=False, default="/hello"),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("created_by", String, nullable=False),
    Column("metadata_json", Text, nullable=False, default="{}"),
    Column("runtime_backend", String, nullable=True),
    Column("runtime_name", String, nullable=True),
    Column("runtime_status", String, nullable=False, default="declared"),
    Column("active_service_id", String, nullable=True),
)

repository_entries_table = Table(
    "repository_entries",
    metadata,
    Column("id", String, primary_key=True),
    Column("name", String, nullable=False),
    Column("kind", String, nullable=False),
    Column("type", String, nullable=False),
    Column("project_id", String, nullable=True),
    Column("owner", String, nullable=False),
    Column("display_name", Text, nullable=False),
    Column("description", Text, nullable=True),
    Column("tags_json", Text, nullable=False, default="[]"),
    Column("status", String, nullable=False, default="draft"),
    Column("published_version", Integer, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

repository_versions_table = Table(
    "repository_versions",
    metadata,
    Column("id", String, primary_key=True),
    Column("entry_id", String, ForeignKey("repository_entries.id"), nullable=False),
    Column("version", Integer, nullable=False),
    Column("kind", String, nullable=False),
    Column("source_hash", String, nullable=False),
    Column("runner_image", Text, nullable=False),
    Column("validation_proof_json", Text, nullable=False, default="{}"),
    Column("approval_status", String, nullable=False, default="draft"),
    Column("status", String, nullable=False, default="draft"),
    Column("approved_by", String, nullable=True),
    Column("approved_at", DateTime(timezone=True), nullable=True),
    Column("published_at", DateTime(timezone=True), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("entry_id", "version", name="uq_repository_versions_entry_version"),
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
