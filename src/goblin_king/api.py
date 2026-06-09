"""FastAPI control plane for Goblin King jobs, schedules, runs, and artifacts."""
# ruff: noqa: B008

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from urllib import error as urlerror
from urllib import request as urlrequest
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import CroniterBadCronError, croniter
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, Security, WebSocket
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from redis import Redis
from redis.exceptions import RedisError

from goblin_king.api_settings import ApiSettings
from goblin_king.auth import (
    AuthError,
    Principal,
    RateLimitExceeded,
    audit,
    authenticate_token,
    check_rate_limit,
    create_api_token,
    create_project,
    create_user,
    require_admin,
    require_project_access,
)
from goblin_king.contracts import (
    ApiTokenRecord,
    ArtifactRecord,
    AuditLogRecord,
    EventRecord,
    HeartbeatRecord,
    JobRecord,
    LongServiceRecord,
    ProjectRecord,
    RunRecord,
    ScheduleRecord,
    UserRecord,
    utc_now,
)
from goblin_king.events import EventBus
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
bearer_scheme = HTTPBearer(auto_error=False)


class ErrorEnvelope(BaseModel):
    """Consistent API error response shape for generated clients."""

    detail: str


class PageMeta(BaseModel):
    """Pagination metadata shared by list responses."""

    limit: int
    offset: int
    count: int


class JobListResponse(BaseModel):
    """Paginated job list response."""

    items: list[JobRecord]
    meta: PageMeta


class EventListResponse(BaseModel):
    """Paginated event list response."""

    items: list[EventRecord]
    meta: PageMeta


class AuditLogListResponse(BaseModel):
    """Paginated audit log list response."""

    items: list[AuditLogRecord]
    meta: PageMeta


class RunListResponse(BaseModel):
    """Paginated run list response."""

    items: list[RunRecord]
    meta: PageMeta


class TokenCreateResponse(BaseModel):
    """API token create response that returns the raw token only once."""

    token: ApiTokenRecord
    raw_token: str


class JobCreateRequest(BaseModel):
    """Request body for queueing one job through the API."""

    kind: str
    input: dict[str, Any] = Field(default_factory=dict)
    priority: int = 100
    project_id: str | None = None
    correlation_id: str | None = None
    max_retries: int = Field(default=0, ge=0)
    timeout_seconds: int | None = Field(default=None, gt=0)


class ScheduleCreateRequest(BaseModel):
    """Request body for creating one recurring schedule."""

    kind: str
    cron: str
    input: dict[str, Any] = Field(default_factory=dict)
    timezone: str = "UTC"
    project_id: str | None = None
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


class UserCreateRequest(BaseModel):
    """Admin request for creating a local API user."""

    email: str
    display_name: str


class ProjectCreateRequest(BaseModel):
    """Admin request for creating a local project."""

    name: str


class TokenCreateRequest(BaseModel):
    """Admin request for creating a local API token."""

    name: str
    user_id: str
    project_id: str | None = None
    role: str = "member"


class LongServiceCreateRequest(BaseModel):
    """Request body for registering a long-running service goblin."""

    kind: str = "example.long-hello"
    base_url: str = "http://localhost:8090"
    project_id: str | None = None


class LongServiceProbeResponse(BaseModel):
    """Response body for a captured long-running service probe."""

    service: LongServiceRecord
    request: dict[str, Any]
    response: dict[str, Any]


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
        self.event_bus = EventBus(store=self.store, redis_url=settings.redis_url)


def create_app(settings: ApiSettings | None = None) -> FastAPI:
    """Create the FastAPI app with loaded Goblin King dependencies."""
    state = AppState(settings or ApiSettings.from_path("goblin-king-api.json"))
    app = FastAPI(
        title="Goblin King API",
        version="0.1.0",
        responses={401: {"model": ErrorEnvelope}, 403: {"model": ErrorEnvelope}},
    )
    app.state.goblin_king = state

    def require_principal(
        request: Request,
        credentials: HTTPAuthorizationCredentials | None = Security(bearer_scheme),
    ) -> Principal:
        """Require a valid bearer token and apply local route rate limits."""
        if credentials is None:
            audit(
                state.store,
                action="auth.failure",
                outcome="denied",
                detail={"route": request.url.path, "reason": "missing token"},
            )
            raise HTTPException(status_code=401, detail="missing or invalid bearer token")
        try:
            principal = authenticate_token(
                state.store,
                credentials.credentials,
                bootstrap_token=state.settings.bootstrap_admin_token,
            )
            check_rate_limit(
                state.store,
                principal=principal,
                route=request.url.path,
                max_per_minute=state.settings.rate_limit_per_minute,
            )
            return principal
        except AuthError as error:
            audit(
                state.store,
                action="auth.failure",
                outcome="denied",
                detail={"route": request.url.path, "reason": str(error)},
            )
            raise HTTPException(status_code=error.status_code, detail=str(error)) from error
        except RateLimitExceeded as error:
            raise HTTPException(status_code=429, detail=str(error)) from error

    def require_admin_principal(principal: Principal = Depends(require_principal)) -> Principal:
        """Require an authenticated admin principal."""
        try:
            require_admin(principal)
            return principal
        except AuthError as error:
            audit(
                state.store,
                action="auth.forbidden",
                outcome="denied",
                principal=principal,
                detail={"reason": str(error)},
            )
            raise HTTPException(status_code=error.status_code, detail=str(error)) from error

    def project_for_request(
        principal: Principal,
        requested_project_id: str | None = None,
    ) -> str | None:
        """Resolve and authorize the project ID for a request."""
        project_id = (
            requested_project_id
            or principal.project_id
            or state.settings.default_project_id
        )
        try:
            require_project_access(principal, project_id)
        except AuthError as error:
            audit(
                state.store,
                action="project.access_denied",
                outcome="denied",
                principal=principal,
                project_id=project_id,
            )
            raise HTTPException(status_code=error.status_code, detail=str(error)) from error
        return project_id

    @app.get("/health", tags=["health"], operation_id="getHealth")
    def health() -> dict[str, str]:
        """Return API health and key local storage paths."""
        return {
            "status": "ok",
            "db": str(state.settings.db),
            "artifact_root": str(state.artifact_root),
        }

    @app.get("/admin", response_class=HTMLResponse, tags=["admin"], operation_id="getAdminUi")
    def admin_ui() -> HTMLResponse:
        """Point direct API users to the separate React admin service."""
        return HTMLResponse(
            """
<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>Goblin King Admin</title></head>
<body>
  <h1>Goblin King React Admin</h1>
  <p>The admin interface is served by the separate React admin service at <code>/admin</code>.</p>
  <p>Use Docker Compose or Helm to run the admin service, then log in with your API token.</p>
</body>
</html>
""",
        )

    @app.post(
        "/admin/users",
        response_model=UserRecord,
        tags=["admin"],
        operation_id="createUser",
    )
    def create_admin_user(
        request: UserCreateRequest,
        principal: Principal = Depends(require_admin_principal),
    ) -> UserRecord:
        """Create one local API user."""
        user = create_user(state.store, email=request.email, display_name=request.display_name)
        audit(
            state.store,
            action="user.created",
            outcome="success",
            principal=principal,
            resource_type="user",
            resource_id=user.id,
        )
        return user

    @app.post(
        "/admin/projects",
        response_model=ProjectRecord,
        tags=["admin"],
        operation_id="createProject",
    )
    def create_admin_project(
        request: ProjectCreateRequest,
        principal: Principal = Depends(require_admin_principal),
    ) -> ProjectRecord:
        """Create one local project."""
        project = create_project(state.store, name=request.name)
        audit(
            state.store,
            action="project.created",
            outcome="success",
            principal=principal,
            project_id=project.id,
            resource_type="project",
            resource_id=project.id,
        )
        return project

    @app.post(
        "/admin/tokens",
        response_model=TokenCreateResponse,
        tags=["admin"],
        operation_id="createApiToken",
    )
    def create_admin_token(
        request: TokenCreateRequest,
        principal: Principal = Depends(require_admin_principal),
    ) -> TokenCreateResponse:
        """Create a hashed API token and return the raw token once."""
        token, raw_token = create_api_token(
            state.store,
            name=request.name,
            user_id=request.user_id,
            project_id=request.project_id,
            role=request.role,
        )
        audit(
            state.store,
            action="token.created",
            outcome="success",
            principal=principal,
            project_id=token.project_id,
            resource_type="token",
            resource_id=token.id,
        )
        return TokenCreateResponse(token=token, raw_token=raw_token)

    @app.post(
        "/services/long-running",
        response_model=LongServiceRecord,
        tags=["services"],
        operation_id="registerLongRunningService",
    )
    def register_long_running_service(
        request: LongServiceCreateRequest,
        principal: Principal = Depends(require_principal),
    ) -> LongServiceRecord:
        """Register a service-style goblin endpoint for admin proof probes."""
        try:
            definition = state.registry.get(request.kind)
            worker = state.workers.get(request.kind)
        except (RegistryError, ValueError) as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        project_id = project_for_request(principal, request.project_id)
        service = LongServiceRecord(
            id=str(uuid4()),
            kind=definition.kind,
            project_id=project_id,
            image=worker.image,
            base_url=request.base_url.rstrip("/"),
            status="registered",
            created_at=utc_now(),
            created_by=principal.user_id,
        )
        state.store.save_long_service(service)
        state.event_bus.emit(
            "admin.service.registered",
            source="api",
            project_id=project_id,
            worker_id=service.id,
            payload={
                "kind": service.kind,
                "base_url": service.base_url,
                "image": service.image,
            },
        )
        audit(
            state.store,
            action="service.registered",
            outcome="success",
            principal=principal,
            project_id=project_id,
            resource_type="long_service",
            resource_id=service.id,
        )
        return service

    @app.get(
        "/services/long-running",
        response_model=list[LongServiceRecord],
        tags=["services"],
        operation_id="listLongRunningServices",
    )
    def list_long_running_services(
        principal: Principal = Depends(require_principal),
    ) -> list[LongServiceRecord]:
        """List registered long-running service goblins visible to the caller."""
        return state.store.list_long_services(project_id=principal.project_id)

    @app.get(
        "/services/long-running/{service_id}",
        response_model=LongServiceRecord,
        tags=["services"],
        operation_id="getLongRunningService",
    )
    def get_long_running_service(
        service_id: str,
        principal: Principal = Depends(require_principal),
    ) -> LongServiceRecord:
        """Return one registered long-running service goblin."""
        service = state.store.get_long_service(service_id)
        if service is None:
            raise HTTPException(status_code=404, detail=f"long service not found: {service_id}")
        project_for_request(principal, service.project_id)
        return service

    @app.post(
        "/services/long-running/{service_id}/probe",
        response_model=LongServiceProbeResponse,
        tags=["services"],
        operation_id="probeLongRunningService",
    )
    def probe_long_running_service(
        service_id: str,
        principal: Principal = Depends(require_principal),
    ) -> LongServiceProbeResponse:
        """Probe one long-running service and persist the request/response proof."""
        service = state.store.get_long_service(service_id)
        if service is None:
            raise HTTPException(status_code=404, detail=f"long service not found: {service_id}")
        project_for_request(principal, service.project_id)
        if service.status == "stopped":
            raise HTTPException(status_code=409, detail=f"long service is stopped: {service_id}")
        probe_url = f"{service.base_url}/hello"
        request_payload = {"method": "GET", "url": probe_url}
        try:
            with urlrequest.urlopen(probe_url, timeout=5) as response:
                response_text = response.read().decode("utf-8")
                response_payload = {
                    "status_code": response.status,
                    "headers": dict(response.headers.items()),
                    "json": json.loads(response_text),
                }
        except (OSError, urlerror.URLError, json.JSONDecodeError) as error:
            response_payload = {"status_code": 0, "error": str(error)}
            updated = state.store.update_long_service_probe(
                service.id,
                status="failed",
                last_probe_at=utc_now(),
                last_probe_json=response_payload,
            )
            state.event_bus.emit(
                "admin.service.probe_failed",
                source="api",
                project_id=service.project_id,
                worker_id=service.id,
                payload={"request": request_payload, "response": response_payload},
            )
            raise HTTPException(status_code=502, detail=str(error)) from error
        updated = state.store.update_long_service_probe(
            service.id,
            status="running",
            last_probe_at=utc_now(),
            last_probe_json=response_payload,
        )
        assert updated is not None
        state.event_bus.emit(
            "admin.service.probed",
            source="api",
            project_id=service.project_id,
            worker_id=service.id,
            payload={"request": request_payload, "response": response_payload},
        )
        audit(
            state.store,
            action="service.probed",
            outcome="success",
            principal=principal,
            project_id=service.project_id,
            resource_type="long_service",
            resource_id=service.id,
            detail={"url": probe_url},
        )
        return LongServiceProbeResponse(
            service=updated,
            request=request_payload,
            response=response_payload,
        )

    @app.post(
        "/services/long-running/{service_id}/stop",
        response_model=LongServiceRecord,
        tags=["services"],
        operation_id="stopLongRunningService",
    )
    def stop_long_running_service(
        service_id: str,
        principal: Principal = Depends(require_principal),
    ) -> LongServiceRecord:
        """Mark a registered long-running service as stopped for tester kill controls."""
        service = state.store.get_long_service(service_id)
        if service is None:
            raise HTTPException(status_code=404, detail=f"long service not found: {service_id}")
        project_for_request(principal, service.project_id)
        if service.status == "stopped":
            return service
        stopped = state.store.update_long_service_status(
            service.id,
            status="stopped",
            last_probe_json={
                "message": "service stopped by King-side admin control",
                "previous_status": service.status,
            },
        )
        assert stopped is not None
        state.event_bus.emit(
            "admin.service.stopped",
            source="api",
            project_id=service.project_id,
            worker_id=service.id,
            payload={"kind": service.kind, "base_url": service.base_url},
        )
        audit(
            state.store,
            action="service.stopped",
            outcome="success",
            principal=principal,
            project_id=service.project_id,
            resource_type="long_service",
            resource_id=service.id,
        )
        return stopped

    @app.get(
        "/audit-logs",
        response_model=AuditLogListResponse,
        tags=["admin"],
        operation_id="listAuditLogs",
    )
    def list_audit_logs(
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
        project_id: str | None = None,
        principal: Principal = Depends(require_principal),
    ) -> AuditLogListResponse:
        """Return audit logs visible to the authenticated principal."""
        scoped_project = project_for_request(principal, project_id)
        items = state.store.list_audit_logs(project_id=scoped_project, limit=limit, offset=offset)
        return AuditLogListResponse(
            items=items,
            meta=PageMeta(limit=limit, offset=offset, count=len(items)),
        )

    @app.get("/goblins", tags=["goblins"], operation_id="listGoblins")
    def list_goblins(_principal: Principal = Depends(require_principal)) -> list[dict[str, Any]]:
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

    @app.post("/jobs", response_model=JobRecord, tags=["jobs"], operation_id="createJob")
    def create_job(
        request: JobCreateRequest,
        principal: Principal = Depends(require_principal),
    ) -> JobRecord:
        """Queue one job for later scheduler execution."""
        try:
            definition = state.registry.get(request.kind)
        except RegistryError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        project_id = project_for_request(principal, request.project_id)
        job = JobRecord(
            id=str(uuid4()),
            kind=definition.kind,
            input=request.input,
            created_at=utc_now(),
            created_by=principal.user_id,
            correlation_id=request.correlation_id,
            project_id=project_id,
            status="queued",
            priority=request.priority,
            due_at=utc_now(),
            max_retries=request.max_retries,
            timeout_seconds=request.timeout_seconds,
        )
        state.store.save_job(job)
        state.event_bus.emit(
            "job.queued",
            source="api",
            project_id=project_id,
            job_id=job.id,
            payload={"kind": job.kind, "created_by": job.created_by},
        )
        audit(
            state.store,
            action="job.created",
            outcome="success",
            principal=principal,
            project_id=project_id,
            resource_type="job",
            resource_id=job.id,
        )
        return job

    @app.get("/jobs", response_model=JobListResponse, tags=["jobs"], operation_id="listJobs")
    def list_jobs(
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
        status: str | None = None,
        project_id: str | None = None,
        principal: Principal = Depends(require_principal),
    ) -> JobListResponse:
        """Return all persisted jobs."""
        scoped_project = project_for_request(principal, project_id)
        items = state.store.list_jobs_page(
            project_id=scoped_project,
            status=status,
            limit=limit,
            offset=offset,
        )
        return JobListResponse(
            items=items,
            meta=PageMeta(limit=limit, offset=offset, count=len(items)),
        )

    @app.get("/jobs/{job_id}", response_model=JobRecord, tags=["jobs"], operation_id="getJob")
    def get_job(
        job_id: str,
        principal: Principal = Depends(require_principal),
    ) -> JobRecord:
        """Return one persisted job."""
        job = state.store.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"job not found: {job_id}")
        project_for_request(principal, job.project_id)
        return job

    @app.post(
        "/jobs/{job_id}/cancel",
        response_model=JobRecord,
        tags=["jobs"],
        operation_id="cancelJob",
    )
    def cancel_job(
        job_id: str,
        principal: Principal = Depends(require_principal),
    ) -> JobRecord:
        """Cancel one non-terminal job."""
        before = state.store.get_job(job_id)
        if before is None:
            raise HTTPException(status_code=404, detail=f"job not found: {job_id}")
        project_for_request(principal, before.project_id)
        if before.status in TERMINAL_JOB_STATUSES:
            raise HTTPException(status_code=409, detail=f"job is terminal: {before.status}")
        cancelled = state.store.cancel_job(job_id)
        if cancelled is None:
            raise HTTPException(status_code=404, detail=f"job not found: {job_id}")
        state.event_bus.emit(
            "job.cancelled",
            source="api",
            project_id=cancelled.project_id,
            job_id=cancelled.id,
            fanout_id=cancelled.fanout_id,
            schedule_id=cancelled.schedule_id,
            payload={"kind": cancelled.kind},
        )
        audit(
            state.store,
            action="job.cancelled",
            outcome="success",
            principal=principal,
            project_id=cancelled.project_id,
            resource_type="job",
            resource_id=cancelled.id,
        )
        return cancelled

    @app.post(
        "/jobs/fanout",
        response_model=FanoutDetail,
        tags=["fanouts"],
        operation_id="createFanout",
    )
    def create_jobs_fanout(
        request: FanoutCreateRequest,
        principal: Principal = Depends(require_principal),
    ) -> FanoutDetail:
        """Create a mixed-kind fanout batch of queued jobs."""
        project_id = project_for_request(principal)
        try:
            detail = create_fanout(
                store=state.store,
                registry=state.registry,
                request=request,
                created_by=principal.user_id,
                project_id=project_id,
            )
            state.event_bus.emit(
                "fanout.created",
                source="api",
                project_id=project_id,
                fanout_id=detail.fanout.id,
                payload={"jobs": [job.id for job in detail.jobs], "count": len(detail.jobs)},
            )
            for job in detail.jobs:
                state.event_bus.emit(
                    "job.queued",
                    source="api",
                    project_id=project_id,
                    job_id=job.id,
                    fanout_id=job.fanout_id,
                    payload={"kind": job.kind, "created_by": job.created_by},
                )
            audit(
                state.store,
                action="fanout.created",
                outcome="success",
                principal=principal,
                project_id=project_id,
                resource_type="fanout",
                resource_id=detail.fanout.id,
            )
            return detail
        except RegistryError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.get("/fanouts", tags=["fanouts"], operation_id="listFanouts")
    def get_fanouts(principal: Principal = Depends(require_principal)) -> list[FanoutDetail]:
        """Return all fanout batches with derived status."""
        details = list_fanout_details(state.store)
        if principal.is_admin and principal.project_id is None:
            return details
        return [detail for detail in details if detail.fanout.project_id == principal.project_id]

    @app.get("/fanouts/{fanout_id}", tags=["fanouts"], operation_id="getFanout")
    def get_fanout(
        fanout_id: str,
        principal: Principal = Depends(require_principal),
    ) -> FanoutDetail:
        """Return one fanout batch with child jobs and runs."""
        try:
            detail = fanout_detail(state.store, fanout_id)
            project_for_request(principal, detail.fanout.project_id)
            return detail
        except KeyError as error:
            raise HTTPException(status_code=404, detail=f"fanout not found: {fanout_id}") from error

    @app.post(
        "/jobs/{job_id}/retry",
        response_model=JobRecord,
        tags=["jobs"],
        operation_id="retryJob",
    )
    def retry_api_job(
        job_id: str,
        request: RetryCreateRequest,
        principal: Principal = Depends(require_principal),
    ) -> JobRecord:
        """Queue a fresh retry job copied from a terminal source job."""
        try:
            source = state.store.get_job(job_id)
            if source is None:
                raise KeyError(job_id)
            project_for_request(principal, source.project_id)
            retry = retry_job(
                store=state.store,
                job_id=job_id,
                request=request,
                created_by=principal.user_id,
            )
            state.event_bus.emit(
                "job.retry_queued",
                source="api",
                project_id=retry.project_id,
                job_id=retry.id,
                fanout_id=retry.fanout_id,
                payload={
                    "kind": retry.kind,
                    "source_job_id": job_id,
                    "reason": request.reason,
                },
            )
            audit(
                state.store,
                action="job.retry_queued",
                outcome="success",
                principal=principal,
                project_id=retry.project_id,
                resource_type="job",
                resource_id=retry.id,
            )
            return retry
        except KeyError as error:
            raise HTTPException(status_code=404, detail=f"job not found: {job_id}") from error
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.post(
        "/schedules",
        response_model=ScheduleRecord,
        tags=["schedules"],
        operation_id="createSchedule",
    )
    def create_schedule(
        request: ScheduleCreateRequest,
        principal: Principal = Depends(require_principal),
    ) -> ScheduleRecord:
        """Create one recurring schedule."""
        project_id = project_for_request(principal, request.project_id)
        schedule = _schedule_from_request(state.registry, request).model_copy(
            update={"project_id": project_id}
        )
        state.store.save_schedule(schedule)
        state.event_bus.emit(
            "schedule.created",
            source="api",
            project_id=project_id,
            schedule_id=schedule.id,
            payload={"kind": schedule.kind, "next_run_at": schedule.next_run_at.isoformat()},
        )
        audit(
            state.store,
            action="schedule.created",
            outcome="success",
            principal=principal,
            project_id=project_id,
            resource_type="schedule",
            resource_id=schedule.id,
        )
        return schedule

    @app.get("/schedules", tags=["schedules"], operation_id="listSchedules")
    def list_schedules(principal: Principal = Depends(require_principal)) -> list[ScheduleRecord]:
        """Return all persisted schedules."""
        schedules = state.store.list_schedules()
        if principal.is_admin and principal.project_id is None:
            return schedules
        return [schedule for schedule in schedules if schedule.project_id == principal.project_id]

    @app.patch(
        "/schedules/{schedule_id}",
        response_model=ScheduleRecord,
        tags=["schedules"],
        operation_id="patchSchedule",
    )
    def patch_schedule(
        schedule_id: str,
        request: SchedulePatchRequest,
        principal: Principal = Depends(require_principal),
    ) -> ScheduleRecord:
        """Patch mutable fields on one schedule."""
        schedule = state.store.get_schedule(schedule_id)
        if schedule is None:
            raise HTTPException(status_code=404, detail=f"schedule not found: {schedule_id}")
        project_for_request(principal, schedule.project_id)

        update = request.model_dump(exclude_unset=True)
        _validate_cron(update.get("cron", schedule.cron))
        _validate_timezone(update.get("timezone", schedule.timezone))
        changed_timing = any(key in update for key in {"cron", "timezone", "enabled"})
        patched = schedule.model_copy(update=update)
        if changed_timing:
            patched = patched.model_copy(update={"next_run_at": next_run_after(patched, utc_now())})
        state.store.update_schedule(patched)
        state.event_bus.emit(
            "schedule.updated",
            source="api",
            project_id=patched.project_id,
            schedule_id=patched.id,
            payload={"kind": patched.kind, "enabled": patched.enabled},
        )
        return patched

    @app.get(
        "/events",
        response_model=EventListResponse,
        tags=["events"],
        operation_id="listEvents",
    )
    def list_events(
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
        event_type: str | None = None,
        after_id: str | None = None,
        job_id: str | None = None,
        run_id: str | None = None,
        fanout_id: str | None = None,
        schedule_id: str | None = None,
        worker_id: str | None = None,
        scheduler_id: str | None = None,
        project_id: str | None = None,
        principal: Principal = Depends(require_principal),
    ) -> EventListResponse:
        """Return durable event history with simple bounded filters."""
        scoped_project = project_for_request(principal, project_id)
        items = state.store.list_events(
            limit=limit,
            offset=offset,
            event_type=event_type,
            after_id=after_id,
            job_id=job_id,
            run_id=run_id,
            fanout_id=fanout_id,
            schedule_id=schedule_id,
            worker_id=worker_id,
            scheduler_id=scheduler_id,
            project_id=scoped_project,
        )
        return EventListResponse(
            items=items,
            meta=PageMeta(limit=limit, offset=offset, count=len(items)),
        )

    @app.websocket("/ws/runs")
    async def stream_runs(websocket: WebSocket) -> None:
        """Stream live event envelopes from Redis pub/sub to WebSocket clients."""
        token = websocket.query_params.get("token")
        if token is None:
            await websocket.close(code=1008)
            return
        try:
            authenticate_token(
                state.store,
                token,
                bootstrap_token=state.settings.bootstrap_admin_token,
            )
        except AuthError:
            await websocket.close(code=1008)
            return
        await websocket.accept()
        pubsub = Redis.from_url(state.settings.redis_url).pubsub()
        try:
            await asyncio.to_thread(pubsub.subscribe, state.event_bus.event_channel)
            while True:
                message = await asyncio.to_thread(pubsub.get_message, True, 1.0)
                if message is None or message.get("type") != "message":
                    continue
                data = message.get("data")
                text = data.decode("utf-8") if isinstance(data, bytes) else str(data)
                await websocket.send_text(text)
        except RedisError as error:
            await websocket.send_json({"error": f"redis pubsub failed: {error}"})
        finally:
            await asyncio.to_thread(pubsub.close)

    @app.get("/heartbeats", tags=["heartbeats"], operation_id="listHeartbeats")
    def list_heartbeats(
        _principal: Principal = Depends(require_principal),
    ) -> list[HeartbeatRecord]:
        """Return scheduler and worker heartbeat records."""
        return state.store.list_heartbeats()

    @app.get("/heartbeats/{owner_id}", tags=["heartbeats"], operation_id="getHeartbeat")
    def get_heartbeat(
        owner_id: str,
        _principal: Principal = Depends(require_principal),
    ) -> HeartbeatRecord:
        """Return one scheduler or worker heartbeat."""
        heartbeat = state.store.get_heartbeat(owner_id)
        if heartbeat is None:
            raise HTTPException(status_code=404, detail=f"heartbeat not found: {owner_id}")
        return heartbeat

    @app.get("/runs/{run_id}", tags=["runs"], operation_id="getRun")
    def get_run(
        run_id: str,
        principal: Principal = Depends(require_principal),
    ) -> Any:
        """Return one persisted run."""
        run = state.store.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"run not found: {run_id}")
        job = state.store.get_job(run.job_id)
        project_for_request(principal, job.project_id if job else run.project_id)
        return run

    @app.get("/runs", response_model=RunListResponse, tags=["runs"], operation_id="listRuns")
    def list_runs(
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
        status: str | None = None,
        kind: str | None = None,
        project_id: str | None = None,
        principal: Principal = Depends(require_principal),
    ) -> RunListResponse:
        """Return persisted runs with bounded filters for the admin UI."""
        scoped_project = project_for_request(principal, project_id)
        items = state.store.list_runs_page(
            project_id=scoped_project,
            status=status,
            kind=kind,
            limit=limit,
            offset=offset,
        )
        return RunListResponse(
            items=items,
            meta=PageMeta(limit=limit, offset=offset, count=len(items)),
        )

    @app.get("/runs/{run_id}/artifacts", tags=["runs"], operation_id="listRunArtifacts")
    def list_run_artifacts(
        run_id: str,
        principal: Principal = Depends(require_principal),
    ) -> list[dict[str, Any]]:
        """Return artifact metadata plus download links for one run."""
        run = state.store.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"run not found: {run_id}")
        job = state.store.get_job(run.job_id)
        project_for_request(principal, job.project_id if job else run.project_id)
        return [
            {
                **artifact.model_dump(mode="json"),
                "download_url": f"/runs/{run_id}/artifacts/{artifact.name}",
            }
            for artifact in state.store.list_run_artifacts(run_id)
        ]

    @app.get(
        "/runs/{run_id}/artifacts/{artifact_name}",
        tags=["runs"],
        operation_id="downloadArtifact",
    )
    def download_artifact(
        run_id: str,
        artifact_name: str,
        principal: Principal = Depends(require_principal),
    ) -> Response:
        """Serve one artifact file if it stays inside the configured artifact root."""
        run = state.store.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"run not found: {run_id}")
        job = state.store.get_job(run.job_id)
        project_for_request(principal, job.project_id if job else run.project_id)
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
