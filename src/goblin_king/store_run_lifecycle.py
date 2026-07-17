"""Persist Runs and reconcile scheduler attempts without overwriting newer job state."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy import Connection, Engine, delete, select, update

from goblin_king.contracts import RunRecord
from goblin_king.store_schema import artifacts_table, handoffs_table, jobs_table, runs_table

AttemptOutcome = Literal["finalized", "cancelled", "stale"]
TERMINAL_RUN_STATUSES = {"completed", "failed", "timed_out"}


@dataclass(frozen=True)
class AttemptFinalization:
    """Describe whether an attempt still owned the job transition it tried to finish."""

    run: RunRecord
    outcome: AttemptOutcome


def normalize_persisted_run(run: RunRecord) -> RunRecord:
    """Give terminal persisted Runs a non-null, non-inverted finish without changing the model."""
    if run.status in TERMINAL_RUN_STATUSES and run.finished_at is None:
        return run.model_copy(update={"finished_at": run.started_at})
    return run


def persist_run(engine: Engine, run: RunRecord) -> RunRecord:
    """Insert one normalized Run and its child metadata."""
    normalized = normalize_persisted_run(run)
    with engine.begin() as connection:
        _insert_run(connection, normalized)
    return normalized


def start_run(engine: Engine, run: RunRecord) -> RunRecord:
    """Insert the one incomplete Run projection exposed during worker execution."""
    if run.status != "running":
        raise ValueError("a started Run must have status='running'")
    if run.finished_at is not None:
        raise ValueError("a started Run cannot have finished_at")
    if run.result is not None:
        raise ValueError("a started Run cannot have a result")
    if run.error is not None:
        raise ValueError("a started Run cannot have an error")
    with engine.begin() as connection:
        _insert_run(connection, run)
    return run


def finalize_attempt(
    engine: Engine,
    run: RunRecord,
    *,
    job_status: str,
    last_error: str | None,
    due_at: datetime | None,
    expected_lease_owner: str,
) -> AttemptFinalization:
    """Persist an attempt and conditionally transition only the job state it still owns."""
    normalized = normalize_persisted_run(run)
    with engine.connect() as connection:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        try:
            row = (
                connection.execute(
                    select(
                        jobs_table.c.status,
                        jobs_table.c.attempt_count,
                        jobs_table.c.lease_owner,
                    ).where(jobs_table.c.id == run.job_id)
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise ValueError(f"job not found while finalizing attempt: {run.job_id}")

            owns_lease = row["lease_owner"] == expected_lease_owner
            owns_running_attempt = (
                row["status"] == "running" and row["attempt_count"] == run.attempt
            )
            owns_leased_attempt = (
                row["status"] == "leased" and row["attempt_count"] + 1 == run.attempt
            )
            if owns_lease and (owns_running_attempt or owns_leased_attempt):
                values: dict[str, object | None] = {
                    "status": job_status,
                    "last_error": last_error,
                    "lease_owner": None,
                    "leased_until": None,
                }
                if due_at is not None:
                    values["due_at"] = due_at
                connection.execute(
                    update(jobs_table).where(jobs_table.c.id == run.job_id).values(**values)
                )
                outcome: AttemptOutcome = "finalized"
            elif row["status"] == "cancelled":
                outcome = "cancelled"
            else:
                outcome = "stale"

            _finalize_run(connection, normalized)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    return AttemptFinalization(run=normalized, outcome=outcome)


def _insert_run(connection: Connection, run: RunRecord) -> None:
    """Insert one Run and its child metadata through an existing transaction."""
    connection.execute(runs_table.insert().values(id=run.id, **_run_values(run)))
    _insert_run_children(connection, run)


def _finalize_run(connection: Connection, run: RunRecord) -> None:
    """Insert a legacy terminal Run or finish its exact running projection once."""
    if run.status not in TERMINAL_RUN_STATUSES:
        raise ValueError(f"cannot finalize Run {run.id!r} with status {run.status!r}")
    existing = (
        connection.execute(
            select(
                runs_table.c.status,
                runs_table.c.job_id,
                runs_table.c.kind,
                runs_table.c.project_id,
                runs_table.c.attempt,
            ).where(runs_table.c.id == run.id)
        )
        .mappings()
        .one_or_none()
    )
    if existing is None:
        _insert_run(connection, run)
        return
    if existing["status"] != "running":
        raise ValueError(
            f"cannot finalize Run {run.id!r} from status {existing['status']!r}"
        )
    expected_lineage = (
        existing["job_id"],
        existing["kind"],
        existing["project_id"],
        existing["attempt"],
    )
    actual_lineage = (run.job_id, run.kind, run.project_id, run.attempt)
    if actual_lineage != expected_lineage:
        raise ValueError(
            f"cannot change Run {run.id!r} lineage while finalizing: "
            f"expected {expected_lineage!r}, received {actual_lineage!r}"
        )
    connection.execute(
        update(runs_table)
        .where(runs_table.c.id == run.id)
        .values(**_run_values(run))
    )
    connection.execute(delete(artifacts_table).where(artifacts_table.c.run_id == run.id))
    connection.execute(delete(handoffs_table).where(handoffs_table.c.run_id == run.id))
    _insert_run_children(connection, run)


def _run_values(run: RunRecord) -> dict[str, object | None]:
    """Return the one authoritative Run row projection shared by insert and finalize."""
    return {
        "job_id": run.job_id,
        "kind": run.kind,
        "project_id": run.project_id,
        "attempt": run.attempt,
        "status": run.status,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "result_json": run.result.model_dump_json() if run.result is not None else None,
        "error": run.error,
        "timeout_seconds": run.timeout_seconds,
        "max_retries": run.max_retries,
        "leased_until": run.leased_until,
        "resource_policy_json": (
            json.dumps(run.resource_policy) if run.resource_policy else None
        ),
    }


def _insert_run_children(connection: Connection, run: RunRecord) -> None:
    """Insert artifact and handoff projections for the current Run result."""
    if run.result is None:
        return
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
