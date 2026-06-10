"""Local smoke flows that prove adopter-facing Goblin King workflows."""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path
from tempfile import mkdtemp
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from goblin_king.contracts import ScheduleRecord
from goblin_king.jsonio import pretty_json_line, read_json_object
from goblin_king.project import ProjectSettings
from goblin_king.registry import GoblinRegistry
from goblin_king.scheduler import Scheduler
from goblin_king.store import SQLiteStore
from goblin_king.templates import init_project
from goblin_king.validation import WorkerValidationResult, validate_workers, validation_record
from goblin_king.workers import WorkerImageMap


class AdopterSmokeResult(BaseModel):
    """Structured proof from the project-adopter smoke flow."""

    ok: bool
    project_dir: str | None = None
    cleanup: str
    validation: list[WorkerValidationResult] = Field(default_factory=list)
    scheduled_kinds: list[str] = Field(default_factory=list)
    runs: list[dict[str, Any]] = Field(default_factory=list)
    result_classes: dict[str, str] = Field(default_factory=dict)
    artifact_count: int = 0
    failure_error: str | None = None


def run_adopter_project_smoke(
    *,
    work_dir: Path | None = None,
    prefix: str = "smoke",
    redis_url: str = "redis://localhost:6379/0",
    keep: bool = False,
) -> AdopterSmokeResult:
    """Generate, validate, schedule, inspect, and clean up a project goblin fixture."""
    root = Path(work_dir) if work_dir is not None else Path(mkdtemp(prefix="goblin-smoke-"))
    created_here = work_dir is None
    root.mkdir(parents=True, exist_ok=True)
    project_dir = init_project(root / "adopter-project", prefix=prefix)
    _add_failure_goblin(project_dir, prefix)

    project_path = project_dir / "goblin-king-project.json"
    settings = ProjectSettings.from_path(project_path)
    registry = GoblinRegistry.from_project_sources(
        settings.registries,
        include_entry_points=settings.entry_points,
        definitions=settings.registry_definitions(),
    )
    workers = WorkerImageMap.from_path_and_definitions(
        settings.images,
        settings.worker_definitions(),
    )

    validation = [
        *validate_workers(
            registry=registry,
            workers=workers,
            input_payload=read_json_object(project_dir / "inputs" / "hello.json"),
            kinds=[f"{prefix}.hello"],
            build=True,
            require_success=True,
            redis_url=redis_url,
        ),
        *validate_workers(
            registry=registry,
            workers=workers,
            input_payload=read_json_object(project_dir / "inputs" / "artifact.json"),
            kinds=[f"{prefix}.artifact"],
            build=True,
            require_success=True,
            redis_url=redis_url,
        ),
        *validate_workers(
            registry=registry,
            workers=workers,
            input_payload=read_json_object(project_dir / "inputs" / "failure.json"),
            kinds=[f"{prefix}.failure"],
            build=True,
            require_success=False,
            redis_url=redis_url,
        ),
    ]

    db_path = project_dir / ".goblin-king" / "smoke.sqlite3"
    store = SQLiteStore(db_path)
    for validation_result in validation:
        store.save_worker_validation(validation_record(validation_result))
    now = datetime.now(UTC)
    schedule_inputs = {
        f"{prefix}.hello": read_json_object(project_dir / "inputs" / "hello.json"),
        f"{prefix}.artifact": read_json_object(project_dir / "inputs" / "artifact.json"),
        f"{prefix}.failure": read_json_object(project_dir / "inputs" / "failure.json"),
    }
    for kind, payload in schedule_inputs.items():
        store.save_schedule(
            ScheduleRecord(
                id=str(uuid4()),
                kind=kind,
                input=payload,
                cron="* * * * *",
                created_at=now,
                next_run_at=now,
            )
        )

    scheduler = Scheduler(
        registry=registry,
        store=store,
        workers=workers,
        runtime_mode="docker",
        redis_url=redis_url,
        worker_id="adopter-smoke",
    )
    runs = scheduler.run_once(now)
    run_summaries = [_run_summary(run) for run in runs]
    result_classes = {item["kind"]: item["status"] for item in run_summaries}
    artifact_count = sum(item["artifacts"] for item in run_summaries)
    failure = next((item for item in run_summaries if item["kind"] == f"{prefix}.failure"), None)

    ok = (
        all(item.ok for item in validation)
        and result_classes.get(f"{prefix}.hello") == "completed"
        and result_classes.get(f"{prefix}.artifact") == "completed"
        and result_classes.get(f"{prefix}.failure") == "failed"
        and artifact_count >= 1
        and failure is not None
        and bool(failure["error"])
    )

    cleanup = "kept"
    project_dir_value: str | None = str(project_dir)
    if created_here and not keep:
        shutil.rmtree(root, ignore_errors=True)
        cleanup = "removed"
        project_dir_value = None

    return AdopterSmokeResult(
        ok=ok,
        project_dir=project_dir_value,
        cleanup=cleanup,
        validation=validation,
        scheduled_kinds=list(schedule_inputs),
        runs=run_summaries,
        result_classes=result_classes,
        artifact_count=artifact_count,
        failure_error=str(failure["error"]) if failure else None,
    )


def _add_failure_goblin(project_dir: Path, prefix: str) -> None:
    """Add a controlled-failure worker to the generated adopter project."""
    kind = f"{prefix}.failure"
    project_path = project_dir / "goblin-king-project.json"
    payload = read_json_object(project_path)
    payload["goblins"][kind] = {
        "image": f"{prefix}-failure:local",
        "context": f"workers/{kind}",
        "dockerfile": "Dockerfile",
        "description": "Controlled failure worker for adopter smoke proof.",
        "labels": {"demo": "true", "kind": "failure"},
        "tags": ["quickstart", "failure"],
        "resourcePolicy": {"timeout_seconds": 30, "memory": {"limit": "256Mi"}},
    }
    project_path.write_text(pretty_json_line(payload), encoding="utf-8")
    (project_dir / "inputs" / "failure.json").write_text(
        pretty_json_line({"reason": "expected adopter smoke failure"}),
        encoding="utf-8",
    )
    worker_dir = project_dir / "workers" / kind
    worker_dir.mkdir(parents=True)
    (worker_dir / "Dockerfile").write_text(_failure_dockerfile(), encoding="utf-8")
    (worker_dir / "worker.py").write_text(_failure_worker(), encoding="utf-8")


def _failure_dockerfile() -> str:
    """Return the Dockerfile for the controlled-failure smoke worker."""
    return """FROM python:3.12-slim

WORKDIR /worker
COPY worker.py /worker/worker.py
RUN pip install --no-cache-dir "redis>=5,<7"

ENTRYPOINT ["python", "/worker/worker.py"]
"""


def _failure_worker() -> str:
    """Return a worker that writes a valid failed result envelope."""
    return '''"""Controlled failure worker for adopter smoke proof."""

from __future__ import annotations

import json
import os
from pathlib import Path

from redis import Redis


def main() -> None:
    """Write a valid failed result envelope and exit nonzero."""
    input_payload = json.loads(Path(os.environ["GOBLIN_INPUT_PATH"]).read_text())
    reason = input_payload.get("reason", "expected failure")
    result = {
        "status": "failed",
        "data": {"expected": True},
        "artifacts": [],
        "metrics": {"controlled_failure": 1},
        "handoff": [],
        "error": reason,
    }
    result_json = json.dumps(result)
    Path(os.environ["GOBLIN_RESULT_PATH"]).write_text(result_json, encoding="utf-8")
    Redis.from_url(os.environ["GOBLIN_REDIS_URL"]).set(
        f"goblin-king:results:{os.environ['GOBLIN_RUN_ID']}",
        result_json,
        ex=3600,
    )
    raise SystemExit(1)


if __name__ == "__main__":
    main()
'''


def _run_summary(run: Any) -> dict[str, Any]:
    """Return the result fields needed by adopter smoke proof."""
    result = run.result
    return {
        "id": run.id,
        "job_id": run.job_id,
        "kind": run.kind,
        "status": run.status,
        "result_status": result.status if result else None,
        "artifacts": len(result.artifacts) if result else 0,
        "error": run.error or (result.error if result else None),
    }
