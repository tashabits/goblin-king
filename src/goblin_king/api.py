"""FastAPI control plane for Goblin King jobs, schedules, runs, and artifacts."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import CroniterBadCronError, croniter
from fastapi import Depends, FastAPI, Header, HTTPException, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from goblin_king.api_settings import ApiSettings
from goblin_king.contracts import ArtifactRecord, JobRecord, ScheduleRecord, utc_now
from goblin_king.fanout import (
    FanoutCreateRequest,
    FanoutDetail,
    RetryCreateRequest,
    create_fanout,
    fanout_detail,
    list_fanout_details,
    retry_job,
)
from goblin_king.project import ProjectSettings
from goblin_king.registry import GoblinRegistry, RegistryError
from goblin_king.scheduler import next_run_after
from goblin_king.store import SQLiteStore
from goblin_king.workers import WorkerImageMap

TERMINAL_JOB_STATUSES = {"completed", "failed", "timed_out", "cancelled"}


class JobCreateRequest(BaseModel):
    """Request body for queueing one job through the API."""

    kind: str
    input: dict[str, Any] = Field(default_factory=dict)
    priority: int = 100
    correlation_id: str | None = None
    max_retries: int = Field(default=0, ge=0)
    timeout_seconds: int | None = Field(default=None, gt=0)


class ScheduleCreateRequest(BaseModel):
    """Request body for creating one recurring schedule."""

    kind: str
    cron: str
    input: dict[str, Any] = Field(default_factory=dict)
    timezone: str = "UTC"
    enabled: bool = True
    priority: int = 100
    max_retries: int = Field(default=0, ge=0)
    timeout_seconds: int | None = Field(default=None, gt=0)
    due_now: bool = False


class SchedulePatchRequest(BaseModel):
    """Partial request body for updating a recurring schedule."""

    cron: str | None = None
    input: dict[str, Any] | None = None
    timezone: str | None = None
    enabled: bool | None = None
    priority: int | None = None
    max_retries: int | None = Field(default=None, ge=0)
    timeout_seconds: int | None = Field(default=None, gt=0)


class AppState:
    """Runtime dependencies shared by API route handlers."""

    def __init__(self, settings: ApiSettings) -> None:
        self.settings = settings
        self.store = SQLiteStore(settings.db)
        if settings.project is not None:
            project = ProjectSettings.from_path(settings.project)
            self.registry = GoblinRegistry.from_project_sources(
                project.registries,
                include_entry_points=project.entry_points,
            )
        else:
            self.registry = GoblinRegistry.from_path(settings.registry)
        self.workers = WorkerImageMap.from_path(settings.images)
        self.artifact_root = settings.artifact_root.resolve()
        self.artifact_root.mkdir(parents=True, exist_ok=True)


def create_app(settings: ApiSettings | None = None) -> FastAPI:
    """Create the FastAPI app with loaded Goblin King dependencies."""
    state = AppState(settings or ApiSettings.from_path("goblin-king-api.json"))
    app = FastAPI(title="Goblin King API", version="0.1.0")
    app.state.goblin_king = state

    def require_token(authorization: str | None = Header(default=None)) -> None:
        """Require the configured bearer token for mutating endpoints."""
        expected = f"Bearer {state.settings.auth_token}"
        if authorization != expected:
            raise HTTPException(status_code=401, detail="missing or invalid bearer token")

    @app.get("/health")
    def health() -> dict[str, str]:
        """Return API health and key local storage paths."""
        return {
            "status": "ok",
            "db": str(state.settings.db),
            "artifact_root": str(state.artifact_root),
        }

    @app.get("/goblins")
    def list_goblins() -> list[dict[str, Any]]:
        """Return registered goblins plus Docker worker mapping availability."""
        payload = []
        mapped = {kind: worker for kind, worker in state.workers.items()}
        for definition in state.registry.list():
            worker = mapped.get(definition.kind)
            payload.append(
                {
                    **definition.model_dump(mode="json"),
                    "worker_image": worker.image if worker else None,
                    "worker_mapped": worker is not None,
                }
            )
        return payload

    @app.post("/jobs", dependencies=[Depends(require_token)])
    def create_job(request: JobCreateRequest) -> JobRecord:
        """Queue one job for later scheduler execution."""
        try:
            definition = state.registry.get(request.kind)
        except RegistryError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        job = JobRecord(
            id=str(uuid4()),
            kind=definition.kind,
            input=request.input,
            created_at=utc_now(),
            created_by="api",
            correlation_id=request.correlation_id,
            status="queued",
            priority=request.priority,
            due_at=utc_now(),
            max_retries=request.max_retries,
            timeout_seconds=request.timeout_seconds,
        )
        state.store.save_job(job)
        return job

    @app.get("/jobs")
    def list_jobs() -> list[JobRecord]:
        """Return all persisted jobs."""
        return state.store.list_jobs()

    @app.get("/jobs/{job_id}")
    def get_job(job_id: str) -> JobRecord:
        """Return one persisted job."""
        job = state.store.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"job not found: {job_id}")
        return job

    @app.post("/jobs/{job_id}/cancel", dependencies=[Depends(require_token)])
    def cancel_job(job_id: str) -> JobRecord:
        """Cancel one non-terminal job."""
        before = state.store.get_job(job_id)
        if before is None:
            raise HTTPException(status_code=404, detail=f"job not found: {job_id}")
        if before.status in TERMINAL_JOB_STATUSES:
            raise HTTPException(status_code=409, detail=f"job is terminal: {before.status}")
        cancelled = state.store.cancel_job(job_id)
        if cancelled is None:
            raise HTTPException(status_code=404, detail=f"job not found: {job_id}")
        return cancelled

    @app.post("/jobs/fanout", dependencies=[Depends(require_token)])
    def create_jobs_fanout(request: FanoutCreateRequest) -> FanoutDetail:
        """Create a mixed-kind fanout batch of queued jobs."""
        try:
            return create_fanout(
                store=state.store,
                registry=state.registry,
                request=request,
                created_by="api",
            )
        except RegistryError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.get("/fanouts")
    def get_fanouts() -> list[FanoutDetail]:
        """Return all fanout batches with derived status."""
        return list_fanout_details(state.store)

    @app.get("/fanouts/{fanout_id}")
    def get_fanout(fanout_id: str) -> FanoutDetail:
        """Return one fanout batch with child jobs and runs."""
        try:
            return fanout_detail(state.store, fanout_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=f"fanout not found: {fanout_id}") from error

    @app.post("/jobs/{job_id}/retry", dependencies=[Depends(require_token)])
    def retry_api_job(job_id: str, request: RetryCreateRequest) -> JobRecord:
        """Queue a fresh retry job copied from a terminal source job."""
        try:
            return retry_job(
                store=state.store,
                job_id=job_id,
                request=request,
                created_by="api-retry",
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail=f"job not found: {job_id}") from error
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.post("/schedules", dependencies=[Depends(require_token)])
    def create_schedule(request: ScheduleCreateRequest) -> ScheduleRecord:
        """Create one recurring schedule."""
        schedule = _schedule_from_request(state.registry, request)
        state.store.save_schedule(schedule)
        return schedule

    @app.get("/schedules")
    def list_schedules() -> list[ScheduleRecord]:
        """Return all persisted schedules."""
        return state.store.list_schedules()

    @app.patch("/schedules/{schedule_id}", dependencies=[Depends(require_token)])
    def patch_schedule(schedule_id: str, request: SchedulePatchRequest) -> ScheduleRecord:
        """Patch mutable fields on one schedule."""
        schedule = state.store.get_schedule(schedule_id)
        if schedule is None:
            raise HTTPException(status_code=404, detail=f"schedule not found: {schedule_id}")

        update = request.model_dump(exclude_unset=True)
        _validate_cron(update.get("cron", schedule.cron))
        _validate_timezone(update.get("timezone", schedule.timezone))
        changed_timing = any(key in update for key in {"cron", "timezone", "enabled"})
        patched = schedule.model_copy(update=update)
        if changed_timing:
            patched = patched.model_copy(update={"next_run_at": next_run_after(patched, utc_now())})
        state.store.update_schedule(patched)
        return patched

    @app.get("/runs/{run_id}")
    def get_run(run_id: str) -> Any:
        """Return one persisted run."""
        run = state.store.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"run not found: {run_id}")
        return run

    @app.get("/runs/{run_id}/artifacts")
    def list_run_artifacts(run_id: str) -> list[dict[str, Any]]:
        """Return artifact metadata plus download links for one run."""
        if state.store.get_run(run_id) is None:
            raise HTTPException(status_code=404, detail=f"run not found: {run_id}")
        return [
            {
                **artifact.model_dump(mode="json"),
                "download_url": f"/runs/{run_id}/artifacts/{artifact.name}",
            }
            for artifact in state.store.list_run_artifacts(run_id)
        ]

    @app.get("/runs/{run_id}/artifacts/{artifact_name}")
    def download_artifact(run_id: str, artifact_name: str) -> Response:
        """Serve one artifact file if it stays inside the configured artifact root."""
        artifacts = state.store.list_run_artifacts(run_id)
        artifact = next((item for item in artifacts if item.name == artifact_name), None)
        if artifact is None:
            raise HTTPException(status_code=404, detail=f"artifact not found: {artifact_name}")
        path = _artifact_file_path(state.artifact_root, artifact)
        if path is None or not path.exists() or not path.is_file():
            raise HTTPException(status_code=404, detail=f"artifact file not found: {artifact_name}")
        return FileResponse(path, media_type=artifact.media_type, filename=artifact.name)

    return app


def _schedule_from_request(
    registry: GoblinRegistry,
    request: ScheduleCreateRequest,
) -> ScheduleRecord:
    """Validate and convert a schedule create body into a persisted record."""
    try:
        definition = registry.get(request.kind)
    except RegistryError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    _validate_cron(request.cron)
    _validate_timezone(request.timezone)
    created_at = utc_now()
    provisional = ScheduleRecord(
        id=str(uuid4()),
        kind=definition.kind,
        input=request.input,
        cron=request.cron,
        timezone=request.timezone,
        enabled=request.enabled,
        priority=request.priority,
        created_at=created_at,
        next_run_at=created_at,
        max_retries=request.max_retries,
        timeout_seconds=request.timeout_seconds,
    )
    return provisional.model_copy(
        update={
            "next_run_at": (
                created_at if request.due_now else next_run_after(provisional, created_at)
            )
        }
    )


def _validate_cron(value: str) -> None:
    """Raise an HTTP 422 for invalid cron expressions."""
    try:
        croniter(value)
    except (CroniterBadCronError, ValueError) as error:
        raise HTTPException(status_code=422, detail=f"invalid cron expression: {value}") from error


def _validate_timezone(value: str) -> None:
    """Raise an HTTP 422 for unknown timezones."""
    try:
        ZoneInfo(value)
    except ZoneInfoNotFoundError as error:
        raise HTTPException(status_code=422, detail=f"unknown timezone: {value}") from error


def _artifact_file_path(root: Path, artifact: ArtifactRecord) -> Path | None:
    """Resolve a file artifact only when it stays inside the configured artifact root."""
    if artifact.uri.startswith("file://"):
        candidate = Path(artifact.uri.removeprefix("file://"))
    elif "://" in artifact.uri:
        return None
    else:
        candidate = Path(artifact.uri)
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        resolved = candidate.resolve()
        resolved.relative_to(root)
    except ValueError:
        return None
    return resolved
