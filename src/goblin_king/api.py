"""FastAPI control plane for Goblin King jobs, schedules, runs, and artifacts."""
# ruff: noqa: B008

from __future__ import annotations

import asyncio
import json
import os
from typing import Any
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest
from uuid import uuid4

from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Query,
    Request,
    Response,
    Security,
    WebSocket,
)
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.responses import Response as FastAPIResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from redis import Redis
from redis.exceptions import RedisError

from goblin_king.api_artifacts import (
    artifact_file_path,
    artifact_storage_status,
)
from goblin_king.api_artifacts import (
    cleanup_artifact_files as cleanup_artifact_files_for_store,
)
from goblin_king.api_models import (
    ArtifactCleanupRequest,
    ArtifactCleanupResponse,
    ArtifactStorageStatusResponse,
    AuditLogListResponse,
    DiscoverySourcesResponse,
    DiscoveryStatusResponse,
    ErrorEnvelope,
    EventListResponse,
    EventStreamStatusResponse,
    HelmTemplateRequest,
    ImagePromotionCreateRequest,
    ImagePromotionUpdateRequest,
    JobCreateRequest,
    JobListResponse,
    LongServiceCreateRequest,
    LongServiceProbeResponse,
    NotebookGoblinCreateRequest,
    NotebookGoblinValidateRequest,
    NotebookGoblinValidateResponse,
    NotebookServiceCreateRequest,
    NotebookServiceStartRequest,
    NotebookServiceStartResponse,
    NotebookServiceStopResponse,
    NotebookServiceValidateRequest,
    NotebookServiceValidateResponse,
    PageMeta,
    ProjectCreateRequest,
    RepositoryDeleteResponse,
    RepositoryEntryDetailResponse,
    RepositoryFunctionRunRequest,
    RepositoryFunctionRunResponse,
    RepositoryListResponse,
    RepositoryPublishRequest,
    RepositoryReviewRequest,
    RepositoryServiceProbeRequest,
    RepositoryServiceProbeResponse,
    RepositoryServiceStartRequest,
    RepositoryServiceStartResponse,
    RepositoryServiceStopRequest,
    RepositoryServiceStopResponse,
    RepositorySubmitRequest,
    RepositorySubmitResponse,
    RepositoryValidateRequest,
    RepositoryValidationResponse,
    RunListResponse,
    RuntimeCleanupRequest,
    RuntimeCleanupResponse,
    RuntimeTerminationRequest,
    RuntimeTerminationResponse,
    ScheduleCreateRequest,
    SchedulePatchRequest,
    TokenCreateRequest,
    TokenCreateResponse,
    UserCreateRequest,
)
from goblin_king.api_runtime import (
    effective_policy,
    record_policy_rejection,
    record_runtime_termination,
)
from goblin_king.api_schedules import (
    schedule_from_request,
    validate_cron,
    validate_timezone,
)
from goblin_king.api_settings import ApiSettings
from goblin_king.api_state import AppState
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
    DeploymentRecord,
    HeartbeatRecord,
    ImagePromotionRecord,
    JobRecord,
    LongServiceRecord,
    NotebookGoblinRecord,
    NotebookServiceRecord,
    ProjectRecord,
    RepositoryEntryRecord,
    RepositoryVersionRecord,
    ScheduleRecord,
    UserRecord,
    utc_now,
)
from goblin_king.deployment import helm_template_command, image_push_command, run_command
from goblin_king.events import DEFAULT_EVENT_STREAM, stream_status
from goblin_king.fanout import (
    FanoutCreateRequest,
    FanoutDetail,
    RetryCreateRequest,
    create_fanout,
    fanout_detail,
    list_fanout_details,
    retry_job,
)
from goblin_king.metadata import goblin_job_metadata
from goblin_king.notebook_services import (
    NotebookServiceRuntimeError,
    NotebookServiceRuntimeManager,
    notebook_service_source_hash,
)
from goblin_king.notebooks import (
    notebook_definition,
    notebook_source_hash,
    notebook_validation_identity,
    notebook_worker_input,
    notebook_worker_map,
)
from goblin_king.project import ProjectSettingsError
from goblin_king.registry import GoblinRegistry, RegistryError
from goblin_king.resource_policies import ResourcePolicyError
from goblin_king.runtime import KubernetesRuntime, new_run_context
from goblin_king.scheduler import next_run_after
from goblin_king.termination import terminate_runtime
from goblin_king.validation import (
    WorkerValidationResult,
    validate_workers,
    validation_job_id,
    validation_record,
    validation_status_payload,
)
from goblin_king.workers import WorkerConfigError

TERMINAL_JOB_STATUSES = {"completed", "failed", "timed_out", "cancelled"}
bearer_scheme = HTTPBearer(auto_error=False)
HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}
SENSITIVE_PROXY_REQUEST_HEADERS = {
    "authorization",
    "cookie",
    "host",
    "proxy-authorization",
    "x-api-key",
    "x-auth-token",
}
SERVICE_PROXY_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]


def _normalize_probe_path(value: str | None) -> str:
    """Return an absolute probe path with a backward-compatible default."""
    probe_path = value or "/hello"
    return probe_path if probe_path.startswith("/") else f"/{probe_path}"


def _service_probe_url(service: LongServiceRecord) -> str:
    """Build the configured probe URL for a registered service."""
    return f"{service.base_url.rstrip('/')}{_normalize_probe_path(service.probe_path)}"


def _service_proxy_url(service: LongServiceRecord, path: str, query: str) -> str:
    """Build a URL under the registered service base URL without changing hosts."""
    encoded_path = urlparse.quote(path, safe="/")
    base = service.base_url.rstrip("/")
    target = f"{base}/{encoded_path}" if encoded_path else base
    return f"{target}?{query}" if query else target


def _proxy_request_headers(request: Request) -> dict[str, str]:
    """Forward non-sensitive client headers to the service workload."""
    return {
        key: value
        for key, value in request.headers.items()
        if key.lower() not in SENSITIVE_PROXY_REQUEST_HEADERS
        and key.lower() not in HOP_BY_HOP_HEADERS
    }


def _proxy_response_headers(headers: Any) -> dict[str, str]:
    """Forward stable upstream response headers through FastAPI."""
    return {
        key: value
        for key, value in dict(headers.items()).items()
        if key.lower() not in HOP_BY_HOP_HEADERS
        and key.lower() not in {"content-length", "set-cookie"}
    }


def _probe_long_service_record(
    state: AppState,
    service: LongServiceRecord,
) -> LongServiceProbeResponse:
    """Probe one registered service and persist its latest proof."""
    probe_url = _service_probe_url(service)
    request_payload = {"method": "GET", "url": probe_url}
    try:
        with urlrequest.urlopen(probe_url, timeout=5) as response:
            response_text = response.read().decode("utf-8")
            response_payload = {
                "status_code": response.status,
                "headers": dict(response.headers.items()),
            }
            try:
                response_payload["json"] = json.loads(response_text)
            except json.JSONDecodeError:
                response_payload["json"] = None
                response_payload["text"] = response_text
    except urlerror.HTTPError as error:
        response_payload = {
            "status_code": error.code,
            "headers": dict(error.headers.items()) if error.headers else {},
            "text": error.read().decode("utf-8", errors="replace"),
            "json": None,
        }
    except OSError as error:
        response_payload = {
            "status_code": None,
            "headers": {},
            "text": str(error),
            "json": None,
        }
    status = (
        "running"
        if isinstance(response_payload.get("status_code"), int)
        and 200 <= response_payload["status_code"] < 300
        else "failed"
    )
    updated = state.store.update_long_service_probe(
        service.id,
        status=status,
        last_probe_at=utc_now(),
        last_probe_json=response_payload,
    )
    assert updated is not None
    state.event_bus.emit(
        "admin.service.probed",
        source="api",
        project_id=service.project_id,
        worker_id=service.id,
        payload={
            "kind": service.kind,
            "status": status,
            "status_code": response_payload.get("status_code"),
        },
    )
    if status == "failed":
        raise HTTPException(
            status_code=502,
            detail={
                "service_id": service.id,
                "probe": response_payload,
            },
        )
    return LongServiceProbeResponse(
        service=updated,
        request=request_payload,
        response=response_payload,
    )


def _forbid_viewer_write(principal: Principal) -> None:
    """Prevent read-only principals from creating runnable notebook bundles."""
    if principal.role == "viewer":
        raise HTTPException(status_code=403, detail="viewer role cannot create notebook goblins")


def _repository_kind_part(value: str | None) -> str:
    """Normalize project/name parts into a valid goblin kind segment."""
    cleaned = []
    for char in (value or "global").lower():
        cleaned.append(char if char.isalnum() else "-")
    part = "".join(cleaned).strip("-")
    while "--" in part:
        part = part.replace("--", "-")
    return part or "global"


def _repository_entry_kind(project_id: str | None, name: str) -> str:
    """Return the stable catalog kind for one repository entry."""
    return f"repository.{_repository_kind_part(project_id)}.{name}"


def _repository_version_kind(project_id: str | None, name: str, version: int) -> str:
    """Return the immutable runtime kind for one repository source version."""
    return f"{_repository_entry_kind(project_id, name)}.v{version}"


def _repository_latest_version(
    entry: RepositoryEntryRecord,
    versions: list[RepositoryVersionRecord],
) -> RepositoryVersionRecord:
    """Return the newest version for an entry or raise a consistent 404."""
    if not versions:
        raise HTTPException(status_code=404, detail=f"repository entry has no versions: {entry.id}")
    return versions[-1]


def _repository_entry_detail(
    state: AppState,
    entry: RepositoryEntryRecord,
) -> RepositoryEntryDetailResponse:
    """Return one repository entry plus all recorded source versions."""
    return RepositoryEntryDetailResponse(
        entry=entry,
        versions=state.store.list_repository_versions(entry.id),
    )


def _repository_requested_version(
    state: AppState,
    entry: RepositoryEntryRecord,
    version: int | None,
) -> RepositoryVersionRecord:
    """Return the requested version or the newest version when omitted."""
    if version is not None:
        record = state.store.get_repository_version(entry.id, version)
        if record is None:
            raise HTTPException(
                status_code=404,
                detail=f"repository version not found: {entry.id}:{version}",
            )
        return record
    return _repository_latest_version(entry, state.store.list_repository_versions(entry.id))


def _require_repository_owner_or_admin(
    principal: Principal,
    entry: RepositoryEntryRecord,
) -> None:
    """Allow repository owners and admins to mutate review workflow state."""
    if principal.role == "admin" or entry.owner == principal.user_id:
        return
    raise HTTPException(status_code=403, detail="repository entry owner or admin required")


def _require_repository_enabled(state: AppState) -> None:
    """Keep repository routes disabled unless the optional service is configured."""
    if not state.settings.repository.enabled:
        raise HTTPException(status_code=404, detail="repository service is not enabled")


def _running_in_kubernetes() -> bool:
    """Return true when the API process is running inside a Kubernetes pod."""
    return bool(os.environ.get("KUBERNETES_SERVICE_HOST"))


def _validate_notebook_with_kubernetes(
    *,
    record: NotebookGoblinRecord,
    input_payload: dict[str, Any],
    require_success: bool,
    timeout_seconds: int | None,
    redis_url: str,
    event_bus: Any,
) -> WorkerValidationResult:
    """Validate a notebook-defined function with an in-cluster Kubernetes Job."""
    runtime = KubernetesRuntime(
        workers=notebook_worker_map(record),
        redis_url=redis_url,
        event_bus=event_bus,
    )
    context = new_run_context(validation_job_id(record.kind), record.kind)
    try:
        run_result = runtime.run(
            notebook_definition(record),
            None,
            input_payload,
            context,
            timeout_seconds=timeout_seconds,
        )
    except Exception as error:
        return WorkerValidationResult(
            kind=record.kind,
            ok=False,
            image=record.image,
            image_digest=notebook_validation_identity(
                f"kubernetes:{record.image}",
                record.source_hash,
            ),
            validated_at=utc_now(),
            error=str(error),
            checks=["kubernetes-job"],
        )
    ok = run_result.status == "success" or not require_success
    return WorkerValidationResult(
        kind=record.kind,
        ok=ok,
        image=record.image,
        image_digest=notebook_validation_identity(
            f"kubernetes:{record.image}",
            record.source_hash,
        ),
        validated_at=utc_now(),
        result_status=run_result.status,
        error=None if ok else run_result.error or "worker returned failed status",
        checks=["kubernetes-job", "result-envelope"],
    )


def _notebook_service_runtime_manager(state: AppState) -> NotebookServiceRuntimeManager:
    """Build the configured notebook ASGI service runtime manager."""
    return NotebookServiceRuntimeManager(
        image=state.settings.notebook_service_image,
        runtime=state.settings.notebook_service_runtime,
        work_root=state.settings.artifact_root.parent / "notebook-services",
    )


def _stop_existing_notebook_service_runtime(
    state: AppState,
    manager: NotebookServiceRuntimeManager,
    record: NotebookServiceRecord,
) -> None:
    """Best-effort cleanup for a previously started notebook service runtime."""
    if record.active_service_id:
        state.store.update_long_service_status(
            record.active_service_id,
            status="stopped",
            last_probe_json={"stopped_by": "notebook_service.restart"},
        )
    if record.runtime_backend and record.runtime_name:
        manager.stop_by_backend(record.runtime_backend, record.runtime_name)
        state.store.update_notebook_service_runtime(
            record.kind,
            runtime_status="stopped",
            runtime_backend=None,
            runtime_name=None,
            active_service_id=None,
            updated_at=utc_now(),
        )


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
                oidc=state.settings.oidc,
                jupyterhub=state.settings.jupyterhub,
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
        "/admin/cleanup/runtime",
        response_model=RuntimeCleanupResponse,
        tags=["admin"],
        operation_id="cleanupRuntimeRows",
    )
    def cleanup_runtime_rows(
        request: RuntimeCleanupRequest,
        principal: Principal = Depends(require_admin_principal),
    ) -> RuntimeCleanupResponse:
        """Preview or delete historical runtime rows without touching auth/project data."""
        project_id = project_for_request(principal, request.project_id)
        counts = state.store.cleanup_runtime_rows(
            project_id=project_id,
            dry_run=request.dry_run,
            include_unprobed_services=request.include_unprobed_services,
        )
        action = "runtime.cleanup.preview" if request.dry_run else "runtime.cleanup"
        if not request.dry_run:
            state.event_bus.emit(
                "admin.runtime.cleaned",
                source="api",
                project_id=project_id,
                payload={"counts": counts},
            )
        audit(
            state.store,
            action=action,
            outcome="success",
            principal=principal,
            project_id=project_id,
            resource_type="runtime_rows",
            detail={
                "dry_run": request.dry_run,
                "include_unprobed_services": request.include_unprobed_services,
                "counts": counts,
            },
        )
        return RuntimeCleanupResponse(
            dry_run=request.dry_run,
            deleted=not request.dry_run,
            counts=counts,
        )

    @app.get(
        "/admin/artifacts/storage",
        response_model=ArtifactStorageStatusResponse,
        tags=["admin"],
        operation_id="getArtifactStorageStatus",
    )
    def get_artifact_storage_status(
        principal: Principal = Depends(require_principal),
    ) -> ArtifactStorageStatusResponse:
        """Return status for the configured volume/PVC artifact root."""
        project_id = project_for_request(principal)
        return ArtifactStorageStatusResponse.model_validate(
            artifact_storage_status(state.store, state.artifact_root, project_id=project_id)
        )

    @app.post(
        "/admin/artifacts/cleanup",
        response_model=ArtifactCleanupResponse,
        tags=["admin"],
        operation_id="cleanupArtifactFiles",
    )
    def cleanup_artifact_files(
        request: ArtifactCleanupRequest,
        principal: Principal = Depends(require_admin_principal),
    ) -> ArtifactCleanupResponse:
        """Preview or delete artifact files from the configured volume/PVC."""
        project_id = project_for_request(principal, request.project_id)
        result = cleanup_artifact_files_for_store(
            state.store,
            state.artifact_root,
            request,
            project_id=project_id,
        )
        action = "artifacts.cleanup.preview" if request.dry_run else "artifacts.cleanup"
        audit(
            state.store,
            action=action,
            outcome="success",
            principal=principal,
            project_id=project_id,
            resource_type="artifact_files",
            detail=result,
        )
        if not request.dry_run:
            state.event_bus.emit(
                "admin.artifacts.cleaned",
                source="api",
                project_id=project_id,
                payload=result,
            )
        return ArtifactCleanupResponse.model_validate(result)

    @app.get(
        "/admin/discovery/status",
        response_model=DiscoveryStatusResponse,
        tags=["admin"],
        operation_id="getDiscoveryStatus",
    )
    def get_discovery_status(
        _principal: Principal = Depends(require_admin_principal),
    ) -> DiscoveryStatusResponse:
        """Return the active deploy-time discovery version and reload health."""
        return state.discovery_status()

    @app.get(
        "/admin/discovery/sources",
        response_model=DiscoverySourcesResponse,
        tags=["admin"],
        operation_id="getDiscoverySources",
    )
    def get_discovery_sources(
        _principal: Principal = Depends(require_admin_principal),
    ) -> DiscoverySourcesResponse:
        """Return registry, entry point, and worker image-map sources."""
        return state.discovery_sources()

    @app.post(
        "/admin/discovery/reload",
        response_model=DiscoveryStatusResponse,
        tags=["admin"],
        operation_id="reloadDiscovery",
    )
    def reload_discovery(
        principal: Principal = Depends(require_admin_principal),
    ) -> DiscoveryStatusResponse:
        """Reload goblin definitions and image mappings without restarting the admin UI."""
        try:
            status = state.reload_discovery()
        except (ProjectSettingsError, RegistryError, WorkerConfigError) as error:
            audit(
                state.store,
                action="discovery.reload",
                outcome="failed",
                principal=principal,
                resource_type="discovery",
                detail={"error": str(error)},
            )
            raise HTTPException(status_code=400, detail=str(error)) from error
        state.event_bus.emit(
            "admin.discovery.reloaded",
            source="api",
            payload={
                "active_goblin_count": status.active_goblin_count,
                "discovery_version": status.discovery_version,
            },
        )
        audit(
            state.store,
            action="discovery.reload",
            outcome="success",
            principal=principal,
            resource_type="discovery",
            detail={
                "active_goblin_count": status.active_goblin_count,
                "discovery_version": status.discovery_version,
            },
        )
        return status

    @app.get(
        "/admin/images/promotions",
        response_model=list[ImagePromotionRecord],
        tags=["admin"],
        operation_id="listImagePromotions",
    )
    def list_image_promotions(
        _principal: Principal = Depends(require_admin_principal),
    ) -> list[ImagePromotionRecord]:
        """Return worker image promotion history for deployment proof."""
        return state.store.list_image_promotions()

    @app.post(
        "/admin/images/promotions",
        response_model=ImagePromotionRecord,
        tags=["admin"],
        operation_id="planImagePromotion",
    )
    def plan_image_promotion(
        request: ImagePromotionCreateRequest,
        principal: Principal = Depends(require_admin_principal),
    ) -> ImagePromotionRecord:
        """Plan or record generic worker image promotion steps."""
        try:
            worker = state.workers.get(request.kind)
        except WorkerConfigError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        now = utc_now()
        source_image = request.source_image or worker.image
        commands: list[list[str]] = []
        if request.build:
            context = state.workers.resolved_context(worker)
            commands.append(
                [
                    "docker",
                    "build",
                    "-f",
                    str(context / worker.dockerfile),
                    "-t",
                    source_image,
                    str(context),
                ]
            )
        if request.push:
            commands.append(image_push_command(request.target_image))
        detail: dict[str, Any] = {
            "dry_run": request.dry_run,
            "commands": commands,
            "worker_context": str(state.workers.resolved_context(worker)),
            "dockerfile": worker.dockerfile,
        }
        promotion = ImagePromotionRecord(
            id=str(uuid4()),
            kind=request.kind,
            source_image=source_image,
            target_image=request.target_image,
            status="planned",
            actor=request.actor,
            created_at=now,
            updated_at=now,
            detail=detail,
        )
        state.store.save_image_promotion(promotion)
        state.event_bus.emit(
            "admin.image_promotion.planned",
            source="api",
            payload=promotion.model_dump(mode="json"),
        )
        audit(
            state.store,
            action="image_promotion.planned",
            outcome="success",
            principal=principal,
            resource_type="image_promotion",
            resource_id=promotion.id,
            detail=detail,
        )
        return promotion

    @app.post(
        "/admin/images/promotions/{promotion_id}/mark",
        response_model=ImagePromotionRecord,
        tags=["admin"],
        operation_id="markImagePromotion",
    )
    def mark_image_promotion(
        promotion_id: str,
        request: ImagePromotionUpdateRequest,
        principal: Principal = Depends(require_admin_principal),
    ) -> ImagePromotionRecord:
        """Mark a worker image promotion as built, pushed, promoted, or failed."""
        promotion = state.store.update_image_promotion(
            promotion_id,
            status=request.status,
            digest=request.digest,
            detail=request.detail,
            updated_at=utc_now(),
        )
        if promotion is None:
            raise HTTPException(status_code=404, detail="image promotion not found")
        state.event_bus.emit(
            f"admin.image_promotion.{promotion.status}",
            source="api",
            payload=promotion.model_dump(mode="json"),
        )
        audit(
            state.store,
            action="image_promotion.marked",
            outcome="success",
            principal=principal,
            resource_type="image_promotion",
            resource_id=promotion.id,
            detail={"status": promotion.status, "digest": promotion.digest},
        )
        return promotion

    @app.get(
        "/admin/deployments",
        response_model=list[DeploymentRecord],
        tags=["admin"],
        operation_id="listDeploymentRecords",
    )
    def list_deployment_records(
        _principal: Principal = Depends(require_admin_principal),
    ) -> list[DeploymentRecord]:
        """Return deployment orchestration proof history."""
        return state.store.list_deployment_records()

    @app.post(
        "/admin/deployments/helm-template",
        response_model=DeploymentRecord,
        tags=["admin"],
        operation_id="recordHelmTemplate",
    )
    def record_helm_template(
        request: HelmTemplateRequest,
        principal: Principal = Depends(require_admin_principal),
    ) -> DeploymentRecord:
        """Record or execute a Helm template proof command."""
        command = helm_template_command(
            chart=request.chart,
            release=request.release,
            namespace=request.namespace,
            values=request.values,
        )
        status = "planned"
        output = None
        detail: dict[str, Any] = {"execute": request.execute}
        if request.execute:
            code, output = run_command(command)
            status = "rendered" if code == 0 else "failed"
            detail["exit_code"] = code
        now = utc_now()
        record = DeploymentRecord(
            id=str(uuid4()),
            name=request.name,
            action="helm-template",
            status=status,
            actor=request.actor,
            command=command,
            output=output,
            created_at=now,
            updated_at=now,
            detail=detail,
        )
        state.store.save_deployment_record(record)
        state.event_bus.emit(
            "admin.deployment.helm_template",
            source="api",
            payload=record.model_dump(mode="json"),
        )
        audit(
            state.store,
            action="deployment.helm_template",
            outcome="success" if status != "failed" else "failed",
            principal=principal,
            resource_type="deployment",
            resource_id=record.id,
            detail=detail,
        )
        return record

    @app.post(
        "/admin/deployments/reload-discovery",
        response_model=DeploymentRecord,
        tags=["admin"],
        operation_id="recordDiscoveryReloadDeployment",
    )
    def record_discovery_reload_deployment(
        principal: Principal = Depends(require_admin_principal),
    ) -> DeploymentRecord:
        """Reload discovery and record the action in the deployment proof trail."""
        try:
            status = state.reload_discovery()
            record_status = "applied"
            detail: dict[str, Any] = status.model_dump(mode="json")
        except (ProjectSettingsError, RegistryError, WorkerConfigError) as error:
            record_status = "failed"
            detail = {"error": str(error)}
        now = utc_now()
        record = DeploymentRecord(
            id=str(uuid4()),
            name="discovery-reload",
            action="discovery-reload",
            status=record_status,
            actor="api",
            command=["goblin-king", "api", "reload-discovery"],
            output=json.dumps(detail),
            created_at=now,
            updated_at=now,
            detail=detail,
        )
        state.store.save_deployment_record(record)
        state.event_bus.emit(
            "admin.deployment.discovery_reload",
            source="api",
            payload=record.model_dump(mode="json"),
        )
        audit(
            state.store,
            action="deployment.discovery_reload",
            outcome="success" if record_status != "failed" else "failed",
            principal=principal,
            resource_type="deployment",
            resource_id=record.id,
            detail=detail,
        )
        if record_status == "failed":
            raise HTTPException(status_code=400, detail=detail["error"])
        return record

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
        definition = None
        worker = None
        try:
            definition = state.registry.get(request.kind)
            worker = state.workers.get(request.kind)
        except (RegistryError, ValueError) as error:
            if not request.base_url:
                raise HTTPException(status_code=404, detail=str(error)) from error
        project_id = project_for_request(principal, request.project_id)
        metadata_probe_path = definition.metadata.get("probe_path") if definition else None
        probe_path = _normalize_probe_path(
            request.probe_path
            or (metadata_probe_path if isinstance(metadata_probe_path, str) else None)
        )
        metadata_base_url = definition.metadata.get("base_url") if definition else None
        base_url = request.base_url or (
            metadata_base_url if isinstance(metadata_base_url, str) else None
        )
        if not base_url:
            raise HTTPException(
                status_code=422,
                detail="base_url is required unless the service metadata defines base_url",
            )
        service = LongServiceRecord(
            id=str(uuid4()),
            kind=definition.kind if definition else request.kind,
            project_id=project_id,
            image=worker.image if worker else request.image or request.kind,
            base_url=base_url.rstrip("/"),
            probe_path=probe_path,
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
                "probe_path": service.probe_path,
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
            raise HTTPException(status_code=409, detail=f"long service is stopped: {service.id}")
        proof = _probe_long_service_record(state, service)
        audit(
            state.store,
            action="service.probed",
            outcome="success",
            principal=principal,
            project_id=service.project_id,
            resource_type="long_service",
            resource_id=service.id,
            detail={"url": proof.request["url"]},
        )
        return proof

    @app.api_route(
        "/services/long-running/{service_id}/proxy",
        methods=SERVICE_PROXY_METHODS,
        tags=["services"],
        operation_id="proxyLongRunningServiceRoot",
        include_in_schema=False,
    )
    @app.api_route(
        "/services/long-running/{service_id}/proxy/{path:path}",
        methods=SERVICE_PROXY_METHODS,
        tags=["services"],
        operation_id="proxyLongRunningService",
        include_in_schema=False,
    )
    async def proxy_long_running_service(
        service_id: str,
        request: Request,
        path: str = "",
        principal: Principal = Depends(require_principal),
    ) -> FastAPIResponse:
        """Proxy authenticated HTTP traffic to a registered service workload."""
        service = state.store.get_long_service(service_id)
        if service is None:
            raise HTTPException(status_code=404, detail=f"long service not found: {service_id}")
        project_for_request(principal, service.project_id)
        return await _proxy_long_service_request(
            service=service,
            request=request,
            path=path,
            principal=principal,
            action="service.proxy",
            resource_type="long_service",
            resource_id=service.id,
        )

    async def _proxy_long_service_request(
        *,
        service: LongServiceRecord,
        request: Request,
        path: str,
        principal: Principal,
        action: str,
        resource_type: str,
        resource_id: str,
        detail_extra: dict[str, Any] | None = None,
        query_string: str | None = None,
    ) -> FastAPIResponse:
        """Forward one authenticated request to a resolved long-running service."""
        if service.status == "stopped":
            audit(
                state.store,
                action=action,
                outcome="denied",
                principal=principal,
                project_id=service.project_id,
                resource_type=resource_type,
                resource_id=resource_id,
                detail={
                    "method": request.method,
                    "path": path,
                    "reason": "stopped",
                    **(detail_extra or {}),
                },
            )
            raise HTTPException(status_code=409, detail=f"long service is stopped: {service.id}")

        target_url = _service_proxy_url(
            service,
            path,
            request.url.query if query_string is None else query_string,
        )
        body = await request.body()
        upstream_request = urlrequest.Request(
            target_url,
            data=body if body else None,
            headers=_proxy_request_headers(request),
            method=request.method,
        )
        detail = {
            "method": request.method,
            "path": path,
            "url": target_url,
            **(detail_extra or {}),
        }
        try:
            with urlrequest.urlopen(upstream_request, timeout=30) as upstream:
                response_body = upstream.read()
                response_headers = _proxy_response_headers(upstream.headers)
                audit(
                    state.store,
                    action=action,
                    outcome="success",
                    principal=principal,
                    project_id=service.project_id,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    detail={**detail, "status_code": upstream.status},
                )
                return FastAPIResponse(
                    content=response_body,
                    status_code=upstream.status,
                    headers=response_headers,
                )
        except urlerror.HTTPError as error:
            response_body = error.read()
            audit(
                state.store,
                action=action,
                outcome="upstream_error",
                principal=principal,
                project_id=service.project_id,
                resource_type=resource_type,
                resource_id=resource_id,
                detail={**detail, "status_code": error.code},
            )
            return FastAPIResponse(
                content=response_body,
                status_code=error.code,
                headers=_proxy_response_headers(error.headers),
            )
        except (OSError, urlerror.URLError) as error:
            audit(
                state.store,
                action=action,
                outcome="failed",
                principal=principal,
                project_id=service.project_id,
                resource_type=resource_type,
                resource_id=resource_id,
                detail={**detail, "error": str(error)},
            )
            raise HTTPException(status_code=502, detail=str(error)) from error

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

    @app.post(
        "/admin/runtime/services/{service_id}/kill",
        response_model=RuntimeTerminationResponse,
        tags=["admin"],
        operation_id="killLongRunningServiceRuntime",
    )
    def kill_long_running_service_runtime(
        service_id: str,
        principal: Principal = Depends(require_admin_principal),
    ) -> RuntimeTerminationResponse:
        """Hard-stop a registered long-running service presentation."""
        service = state.store.get_long_service(service_id)
        if service is None:
            raise HTTPException(status_code=404, detail=f"long service not found: {service_id}")
        project_for_request(principal, service.project_id)
        state.store.update_long_service_status(
            service.id,
            status="stopped",
            last_probe_json={
                "message": "service stopped by scoped runtime termination control",
                "previous_status": service.status,
            },
        )
        return record_runtime_termination(
            state,
            principal=principal,
            project_id=service.project_id,
            target_type="long_service",
            target_id=service_id,
            runtime="both",
            killed=[f"registered-service:{service_id}"],
            errors=[],
            cancelled=True,
        )

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
    def list_goblins(principal: Principal = Depends(require_principal)) -> list[dict[str, Any]]:
        """Return registered goblins plus Docker worker mapping availability."""
        payload = []
        mapped = {kind: worker for kind, worker in state.workers.items()}
        for definition in state.registry.list():
            worker = mapped.get(definition.kind)
            validation = state.store.latest_worker_validation_for_kind(definition.kind)
            payload.append(
                {
                    **definition.model_dump(mode="json"),
                    "worker_image": worker.image if worker else None,
                    "worker_mapped": worker is not None,
                    "validation": validation.model_dump(mode="json") if validation else None,
                    "validation_status": validation_status_payload(
                        worker_image=worker.image if worker else None,
                        validation=validation,
                    ),
                    "source": "project-config"
                    if definition.kind in state._project_defined_kinds
                    else "registry",
                    "project_defaults_resources": state._project_default_resources,
                }
            )
        for record in state.store.list_notebook_goblins():
            try:
                project_for_request(principal, record.project_id)
            except HTTPException:
                continue
            validation = state.store.latest_worker_validation_for_kind(record.kind)
            payload.append(
                {
                    **notebook_definition(record).model_dump(mode="json"),
                    "worker_image": record.image,
                    "worker_mapped": True,
                    "validation": validation.model_dump(mode="json") if validation else None,
                    "validation_status": validation_status_payload(
                        worker_image=record.image,
                        validation=validation,
                    ),
                    "source": "notebook",
                    "project_defaults_resources": state._project_default_resources,
                    "notebook": {
                        "source_hash": record.source_hash,
                        "function_name": record.function_name,
                        "project_id": record.project_id,
                    },
                }
            )
        return payload

    @app.post(
        "/notebooks/goblins",
        response_model=NotebookGoblinRecord,
        tags=["notebooks"],
        operation_id="createNotebookGoblin",
    )
    def create_notebook_goblin(
        request: NotebookGoblinCreateRequest,
        principal: Principal = Depends(require_principal),
    ) -> NotebookGoblinRecord:
        """Build a notebook-defined Python function into a runnable goblin bundle."""
        _forbid_viewer_write(principal)
        try:
            state.registry.get(request.kind)
        except RegistryError:
            pass
        else:
            raise HTTPException(
                status_code=409,
                detail=f"goblin kind already exists in registry: {request.kind}",
            )
        project_id = project_for_request(principal, request.project_id)
        now = utc_now()
        existing = state.store.get_notebook_goblin(request.kind)
        if existing is not None:
            project_for_request(principal, existing.project_id)
            if existing.project_id != project_id:
                raise HTTPException(
                    status_code=409,
                    detail="notebook goblin kind already belongs to another project",
                )
        record = NotebookGoblinRecord(
            kind=request.kind,
            project_id=project_id,
            display_name=request.display_name or request.kind,
            image=request.image or state.settings.notebook_function_image,
            source=request.source,
            source_hash=notebook_source_hash(request.source, request.function_name),
            function_name=request.function_name,
            timeout_seconds=request.timeout_seconds,
            max_retries=request.max_retries,
            created_at=existing.created_at if existing else now,
            updated_at=now,
            created_by=existing.created_by if existing else principal.user_id,
            metadata=request.metadata,
        )
        state.store.save_notebook_goblin(record)
        audit(
            state.store,
            action="notebook_goblin.built",
            outcome="success",
            principal=principal,
            project_id=project_id,
            resource_type="notebook_goblin",
            resource_id=record.kind,
            detail={
                "image": record.image,
                "source_hash": record.source_hash,
                "function_name": record.function_name,
            },
        )
        return record

    @app.get(
        "/notebooks/goblins",
        response_model=list[NotebookGoblinRecord],
        tags=["notebooks"],
        operation_id="listNotebookGoblins",
    )
    def list_notebook_goblins(
        principal: Principal = Depends(require_principal),
    ) -> list[NotebookGoblinRecord]:
        """Return notebook-defined function goblins visible to the caller."""
        records = []
        for record in state.store.list_notebook_goblins():
            try:
                project_for_request(principal, record.project_id)
            except HTTPException:
                continue
            records.append(record)
        return records

    @app.post(
        "/notebooks/goblins/{kind}/validate",
        response_model=NotebookGoblinValidateResponse,
        tags=["notebooks"],
        operation_id="validateNotebookGoblin",
    )
    def validate_notebook_goblin(
        kind: str,
        request: NotebookGoblinValidateRequest,
        principal: Principal = Depends(require_principal),
    ) -> NotebookGoblinValidateResponse:
        """Run contract validation for one notebook-defined function goblin."""
        _forbid_viewer_write(principal)
        record = state.store.get_notebook_goblin(kind)
        if record is None:
            raise HTTPException(status_code=404, detail=f"notebook goblin not found: {kind}")
        project_for_request(principal, record.project_id)
        input_payload = notebook_worker_input(record, request.input)
        timeout_seconds = request.timeout_seconds or record.timeout_seconds
        if _running_in_kubernetes():
            result = _validate_notebook_with_kubernetes(
                record=record,
                input_payload=input_payload,
                require_success=request.require_success,
                timeout_seconds=timeout_seconds,
                redis_url=state.settings.redis_url,
                event_bus=state.event_bus,
            )
        else:
            results = validate_workers(
                registry=GoblinRegistry.from_definitions([notebook_definition(record)]),
                workers=notebook_worker_map(record),
                input_payload=input_payload,
                kinds=[record.kind],
                require_success=request.require_success,
                prebuilt_image=True,
                timeout_seconds=timeout_seconds,
                redis_url=state.settings.redis_url,
            )
            result = results[0]
            result = result.model_copy(
                update={
                    "image_digest": notebook_validation_identity(
                        result.image_digest,
                        record.source_hash,
                    )
                }
            )
        state.store.save_worker_validation(validation_record(result))
        audit(
            state.store,
            action="notebook_goblin.validated",
            outcome="success" if result.ok else "failure",
            principal=principal,
            project_id=record.project_id,
            resource_type="notebook_goblin",
            resource_id=record.kind,
            detail={"image": record.image, "source_hash": record.source_hash, "ok": result.ok},
        )
        return NotebookGoblinValidateResponse(goblin=record, validation=result)

    @app.post(
        "/notebooks/services",
        response_model=NotebookServiceRecord,
        tags=["notebooks"],
        operation_id="createNotebookService",
    )
    def create_notebook_service(
        request: NotebookServiceCreateRequest,
        principal: Principal = Depends(require_principal),
    ) -> NotebookServiceRecord:
        """Declare a notebook-defined ASGI service bundle."""
        _forbid_viewer_write(principal)
        try:
            state.registry.get(request.kind)
        except RegistryError:
            pass
        else:
            raise HTTPException(
                status_code=409,
                detail=f"goblin kind already exists in registry: {request.kind}",
            )
        if state.store.get_notebook_goblin(request.kind) is not None:
            raise HTTPException(
                status_code=409,
                detail=f"notebook function goblin already exists: {request.kind}",
            )
        project_id = project_for_request(principal, request.project_id)
        now = utc_now()
        existing = state.store.get_notebook_service(request.kind)
        if existing is not None:
            project_for_request(principal, existing.project_id)
            if existing.project_id != project_id:
                raise HTTPException(
                    status_code=409,
                    detail="notebook service kind already belongs to another project",
                )
            if existing.active_service_id and existing.runtime_status == "running":
                raise HTTPException(
                    status_code=409,
                    detail="stop the notebook service before declaring new source",
                )
        probe_path = _normalize_probe_path(request.probe_path)
        record = NotebookServiceRecord(
            kind=request.kind,
            project_id=project_id,
            display_name=request.display_name or request.kind,
            image=request.image or state.settings.notebook_service_image,
            source=request.source,
            source_hash=notebook_service_source_hash(
                request.source,
                request.app_name,
                request.requirements,
            ),
            app_name=request.app_name,
            requirements=request.requirements,
            port=request.port,
            probe_path=probe_path,
            created_at=existing.created_at if existing else now,
            updated_at=now,
            created_by=existing.created_by if existing else principal.user_id,
            metadata=request.metadata,
        )
        state.store.save_notebook_service(record)
        audit(
            state.store,
            action="notebook_service.declared",
            outcome="success",
            principal=principal,
            project_id=project_id,
            resource_type="notebook_service",
            resource_id=record.kind,
            detail={
                "image": record.image,
                "source_hash": record.source_hash,
                "app_name": record.app_name,
                "probe_path": record.probe_path,
            },
        )
        return record

    @app.get(
        "/notebooks/services",
        response_model=list[NotebookServiceRecord],
        tags=["notebooks"],
        operation_id="listNotebookServices",
    )
    def list_notebook_services(
        principal: Principal = Depends(require_principal),
    ) -> list[NotebookServiceRecord]:
        """Return notebook-defined ASGI services visible to the caller."""
        records = []
        for record in state.store.list_notebook_services():
            try:
                project_for_request(principal, record.project_id)
            except HTTPException:
                continue
            records.append(record)
        return records

    @app.post(
        "/notebooks/services/{kind}/validate",
        response_model=NotebookServiceValidateResponse,
        tags=["notebooks"],
        operation_id="validateNotebookService",
    )
    def validate_notebook_service(
        kind: str,
        request: NotebookServiceValidateRequest,
        principal: Principal = Depends(require_principal),
    ) -> NotebookServiceValidateResponse:
        """Validate a notebook-defined ASGI service by starting and probing it."""
        _forbid_viewer_write(principal)
        record = state.store.get_notebook_service(kind)
        if record is None:
            raise HTTPException(status_code=404, detail=f"notebook service not found: {kind}")
        project_for_request(principal, record.project_id)
        manager = _notebook_service_runtime_manager(state)
        try:
            runtime = manager.validate(record, timeout_seconds=request.timeout_seconds)
        except NotebookServiceRuntimeError as error:
            state.store.update_notebook_service_runtime(
                record.kind,
                runtime_status="failed",
                updated_at=utc_now(),
            )
            audit(
                state.store,
                action="notebook_service.validated",
                outcome="failure",
                principal=principal,
                project_id=record.project_id,
                resource_type="notebook_service",
                resource_id=record.kind,
                detail={"error": str(error), "source_hash": record.source_hash},
            )
            raise HTTPException(status_code=422, detail=str(error)) from error
        updated = state.store.update_notebook_service_runtime(
            record.kind,
            runtime_status="validated",
            updated_at=utc_now(),
        )
        assert updated is not None
        audit(
            state.store,
            action="notebook_service.validated",
            outcome="success",
            principal=principal,
            project_id=updated.project_id,
            resource_type="notebook_service",
            resource_id=updated.kind,
            detail={"runtime": runtime.model(), "source_hash": updated.source_hash},
        )
        return NotebookServiceValidateResponse(
            service=updated,
            ok=True,
            runtime=runtime.model(),
        )

    @app.post(
        "/notebooks/services/{kind}/start",
        response_model=NotebookServiceStartResponse,
        tags=["notebooks"],
        operation_id="startNotebookService",
    )
    def start_notebook_service(
        kind: str,
        request: NotebookServiceStartRequest,
        principal: Principal = Depends(require_principal),
    ) -> NotebookServiceStartResponse:
        """Start a notebook-defined ASGI service and register it for gated access."""
        _forbid_viewer_write(principal)
        record = state.store.get_notebook_service(kind)
        if record is None:
            raise HTTPException(status_code=404, detail=f"notebook service not found: {kind}")
        project_for_request(principal, record.project_id)
        manager = _notebook_service_runtime_manager(state)
        _stop_existing_notebook_service_runtime(state, manager, record)
        try:
            runtime = manager.start(record, timeout_seconds=request.timeout_seconds)
        except NotebookServiceRuntimeError as error:
            state.store.update_notebook_service_runtime(
                record.kind,
                runtime_status="failed",
                updated_at=utc_now(),
            )
            audit(
                state.store,
                action="notebook_service.started",
                outcome="failure",
                principal=principal,
                project_id=record.project_id,
                resource_type="notebook_service",
                resource_id=record.kind,
                detail={"error": str(error), "source_hash": record.source_hash},
            )
            raise HTTPException(status_code=422, detail=str(error)) from error
        service = LongServiceRecord(
            id=str(uuid4()),
            kind=record.kind,
            project_id=record.project_id,
            image=record.image,
            base_url=runtime.base_url.rstrip("/"),
            probe_path=record.probe_path,
            status="registered",
            created_at=utc_now(),
            created_by=principal.user_id,
        )
        state.store.save_long_service(service)
        updated = state.store.update_notebook_service_runtime(
            record.kind,
            runtime_status="running",
            runtime_backend=runtime.backend,
            runtime_name=runtime.name,
            active_service_id=service.id,
            updated_at=utc_now(),
        )
        assert updated is not None
        try:
            probe = _probe_long_service_record(state, service)
        except HTTPException:
            manager.stop_by_backend(runtime.backend, runtime.name)
            state.store.update_notebook_service_runtime(
                record.kind,
                runtime_status="failed",
                runtime_backend=runtime.backend,
                runtime_name=runtime.name,
                active_service_id=service.id,
                updated_at=utc_now(),
            )
            raise
        state.event_bus.emit(
            "notebook.service.started",
            source="api",
            project_id=updated.project_id,
            worker_id=service.id,
            payload={
                "kind": updated.kind,
                "runtime": runtime.model(),
                "probe_status": probe.response.get("status_code"),
            },
        )
        audit(
            state.store,
            action="notebook_service.started",
            outcome="success",
            principal=principal,
            project_id=updated.project_id,
            resource_type="notebook_service",
            resource_id=updated.kind,
            detail={"service_id": service.id, "runtime": runtime.model()},
        )
        return NotebookServiceStartResponse(
            notebook_service=updated,
            service=probe.service,
            runtime=runtime.model(),
            probe=probe,
        )

    @app.post(
        "/notebooks/services/{kind}/stop",
        response_model=NotebookServiceStopResponse,
        tags=["notebooks"],
        operation_id="stopNotebookService",
    )
    def stop_notebook_service(
        kind: str,
        principal: Principal = Depends(require_principal),
    ) -> NotebookServiceStopResponse:
        """Stop a managed notebook-defined ASGI service and mark it stopped."""
        _forbid_viewer_write(principal)
        record = state.store.get_notebook_service(kind)
        if record is None:
            raise HTTPException(status_code=404, detail=f"notebook service not found: {kind}")
        project_for_request(principal, record.project_id)
        manager = _notebook_service_runtime_manager(state)
        runtime = manager.stop(record)
        service = None
        if record.active_service_id:
            service = state.store.update_long_service_status(
                record.active_service_id,
                status="stopped",
                last_probe_json={"stopped_by": "notebook_service.stop"},
            )
        updated = state.store.update_notebook_service_runtime(
            record.kind,
            runtime_status="stopped",
            runtime_backend=None,
            runtime_name=None,
            active_service_id=None,
            updated_at=utc_now(),
        )
        assert updated is not None
        audit(
            state.store,
            action="notebook_service.stopped",
            outcome="success",
            principal=principal,
            project_id=updated.project_id,
            resource_type="notebook_service",
            resource_id=updated.kind,
            detail=runtime,
        )
        return NotebookServiceStopResponse(
            notebook_service=updated,
            service=service,
            runtime=runtime,
        )

    def _resolve_published_repository_version(
        *,
        name: str,
        expected_type: str,
        principal: Principal,
        project_id: str | None,
        version: int | None,
    ) -> tuple[RepositoryEntryRecord, RepositoryVersionRecord]:
        """Resolve one published repository entry/version visible to a caller."""
        _require_repository_enabled(state)
        scoped_project = project_for_request(principal, project_id)
        entry = state.store.get_repository_entry_by_project_name(scoped_project, name)
        if entry is None:
            raise HTTPException(
                status_code=404,
                detail=f"published repository entry not found: {name}",
            )
        if entry.type != expected_type:
            raise HTTPException(
                status_code=409,
                detail=f"repository entry is {entry.type}, not {expected_type}",
            )
        requested_version = version or entry.published_version
        if requested_version is None:
            published_versions = [
                item
                for item in state.store.list_repository_versions(entry.id)
                if item.status == "published"
            ]
            if not published_versions:
                raise HTTPException(
                    status_code=409,
                    detail=f"repository entry has no published version: {name}",
                )
            return entry, published_versions[-1]
        resolved = state.store.get_repository_version(entry.id, requested_version)
        if resolved is None:
            raise HTTPException(
                status_code=404,
                detail=f"repository version not found: {name}:{requested_version}",
            )
        if resolved.status != "published":
            raise HTTPException(
                status_code=409,
                detail=f"repository version is not published: {name}:{requested_version}",
            )
        return entry, resolved

    def _queue_job(
        request: JobCreateRequest,
        principal: Principal,
        *,
        repository_entry: RepositoryEntryRecord | None = None,
        repository_version: RepositoryVersionRecord | None = None,
    ) -> JobRecord:
        """Queue a registry or notebook-backed job with consistent metadata."""
        notebook_record = None
        try:
            definition = state.registry.get(request.kind)
        except RegistryError as error:
            notebook_record = state.store.get_notebook_goblin(request.kind)
            if notebook_record is None:
                raise HTTPException(status_code=404, detail=str(error)) from error
            definition = notebook_definition(notebook_record)
        project_id = project_for_request(
            principal,
            request.project_id or (notebook_record.project_id if notebook_record else None),
        )
        if notebook_record is not None:
            if request.project_id is not None and request.project_id != notebook_record.project_id:
                raise HTTPException(
                    status_code=403,
                    detail="notebook goblin cannot be submitted outside its project",
                )
            policy = None
            metadata = {
                **goblin_job_metadata(definition),
                "goblin_source": "notebook",
                "notebook_source_hash": notebook_record.source_hash,
                "notebook_function_name": notebook_record.function_name,
            }
            max_retries = request.max_retries or notebook_record.max_retries
            timeout_seconds = request.timeout_seconds or notebook_record.timeout_seconds
        else:
            try:
                policy = effective_policy(
                    state,
                    definition.kind,
                    timeout_seconds=request.timeout_seconds,
                    max_retries=request.max_retries,
                )
            except ResourcePolicyError as error:
                record_policy_rejection(state, principal, project_id, definition.kind, str(error))
                raise HTTPException(status_code=422, detail=str(error)) from error
            metadata = goblin_job_metadata(definition, policy)
            max_retries = (policy.max_retries or 0) if policy else request.max_retries
            timeout_seconds = policy.timeout_seconds if policy else request.timeout_seconds
        if repository_entry is not None and repository_version is not None:
            metadata.update(
                {
                    "goblin_source": "repository",
                    "repository_entry_id": repository_entry.id,
                    "repository_name": repository_entry.name,
                    "repository_version": repository_version.version,
                    "repository_source_hash": repository_version.source_hash,
                }
            )
        created_at = utc_now()
        job = JobRecord(
            id=str(uuid4()),
            kind=definition.kind,
            input=request.input,
            created_at=created_at,
            created_by=principal.user_id,
            correlation_id=request.correlation_id,
            project_id=project_id,
            status="queued",
            priority=request.priority,
            due_at=created_at,
            max_retries=max_retries,
            timeout_seconds=timeout_seconds,
            metadata=metadata,
        )
        state.store.save_job(job)
        state.event_bus.emit(
            "job.queued",
            source="api",
            project_id=project_id,
            job_id=job.id,
            payload={"kind": job.kind, "created_by": job.created_by},
            after=job.created_at,
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

    @app.post(
        "/repository/entries",
        response_model=RepositorySubmitResponse,
        tags=["repository"],
        operation_id="submitRepositoryEntry",
    )
    def submit_repository_entry(
        request: RepositorySubmitRequest,
        principal: Principal = Depends(require_principal),
    ) -> RepositorySubmitResponse:
        """Submit notebook-authored source as a private repository draft version."""
        _require_repository_enabled(state)
        _forbid_viewer_write(principal)
        project_id = project_for_request(principal, request.project_id)
        now = utc_now()
        existing = state.store.get_repository_entry_by_project_name(
            project_id,
            request.name,
        )
        if existing is not None:
            project_for_request(principal, existing.project_id)
            _require_repository_owner_or_admin(principal, existing)
            if existing.type != request.type:
                raise HTTPException(
                    status_code=409,
                    detail="repository entry already exists with a different type",
                )
            entry = state.store.update_repository_entry(
                existing.model_copy(
                    update={
                        "display_name": request.display_name or existing.display_name,
                        "description": request.description,
                        "tags": request.tags,
                        "updated_at": now,
                    }
                )
            )
            next_version = (
                _repository_latest_version(
                    entry,
                    state.store.list_repository_versions(entry.id),
                ).version
                + 1
            )
        else:
            entry = state.store.create_repository_entry(
                RepositoryEntryRecord(
                    id=str(uuid4()),
                    name=request.name,
                    kind=_repository_entry_kind(project_id, request.name),
                    type=request.type,
                    project_id=project_id,
                    owner=principal.user_id,
                    display_name=request.display_name or request.name,
                    description=request.description,
                    tags=request.tags,
                    created_at=now,
                    updated_at=now,
                )
            )
            next_version = 1

        version_kind = _repository_version_kind(project_id, entry.name, next_version)
        if request.type == "notebook_function":
            source_hash = notebook_source_hash(request.source, request.function_name)
            runner_image = request.image or state.settings.notebook_function_image
            notebook = NotebookGoblinRecord(
                kind=version_kind,
                project_id=project_id,
                display_name=request.display_name or entry.display_name,
                image=runner_image,
                source=request.source,
                source_hash=source_hash,
                function_name=request.function_name,
                timeout_seconds=request.timeout_seconds,
                max_retries=request.max_retries,
                created_at=now,
                updated_at=now,
                created_by=principal.user_id,
                metadata={
                    **request.metadata,
                    "repository_entry_id": entry.id,
                    "repository_name": entry.name,
                    "repository_version": next_version,
                },
            )
            state.store.save_notebook_goblin(notebook)
        else:
            probe_path = _normalize_probe_path(request.probe_path)
            source_hash = notebook_service_source_hash(
                request.source,
                request.app_name,
                request.requirements,
            )
            runner_image = request.image or state.settings.notebook_service_image
            notebook = NotebookServiceRecord(
                kind=version_kind,
                project_id=project_id,
                display_name=request.display_name or entry.display_name,
                image=runner_image,
                source=request.source,
                source_hash=source_hash,
                app_name=request.app_name,
                requirements=request.requirements,
                port=request.port,
                probe_path=probe_path,
                created_at=now,
                updated_at=now,
                created_by=principal.user_id,
                metadata={
                    **request.metadata,
                    "repository_entry_id": entry.id,
                    "repository_name": entry.name,
                    "repository_version": next_version,
                },
            )
            state.store.save_notebook_service(notebook)

        try:
            version = state.store.create_repository_version(
                RepositoryVersionRecord(
                    id=str(uuid4()),
                    entry_id=entry.id,
                    version=next_version,
                    kind=version_kind,
                    source_hash=source_hash,
                    runner_image=runner_image,
                    created_at=now,
                    updated_at=now,
                )
            )
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        refreshed = state.store.get_repository_entry(entry.id)
        assert refreshed is not None
        audit(
            state.store,
            action="repository.submit",
            outcome="success",
            principal=principal,
            project_id=project_id,
            resource_type="repository_entry",
            resource_id=entry.id,
            detail={
                "name": entry.name,
                "type": entry.type,
                "version": version.version,
                "kind": version.kind,
                "source_hash": version.source_hash,
            },
        )
        return RepositorySubmitResponse(entry=refreshed, version=version, notebook=notebook)

    @app.get(
        "/repository/entries",
        response_model=RepositoryListResponse,
        tags=["repository"],
        operation_id="listRepositoryEntries",
    )
    def list_repository_entries(
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
        project_id: str | None = None,
        entry_type: str | None = Query(default=None, alias="type"),
        status: str | None = "published",
        q: str | None = None,
        principal: Principal = Depends(require_principal),
    ) -> RepositoryListResponse:
        """List repository entries visible to the authenticated caller."""
        _require_repository_enabled(state)
        scoped_project = project_for_request(principal, project_id)
        requested_status = None if status in {None, "all"} else status
        entries = state.store.list_repository_entries(
            project_id=scoped_project,
            status=requested_status,
            entry_type=entry_type,
            include_retired=False,
        )
        query = (q or "").strip().lower()
        if query:
            entries = [
                entry
                for entry in entries
                if query in entry.name.lower()
                or query in entry.display_name.lower()
                or any(query in tag for tag in entry.tags)
                or (entry.description is not None and query in entry.description.lower())
            ]
        visible = []
        for entry in entries:
            if entry.status != "published":
                try:
                    _require_repository_owner_or_admin(principal, entry)
                except HTTPException:
                    continue
            visible.append(_repository_entry_detail(state, entry))
        page = visible[offset : offset + limit]
        return RepositoryListResponse(
            items=page,
            meta=PageMeta(limit=limit, offset=offset, count=len(page)),
        )

    @app.get(
        "/repository/entries/{entry_id}",
        response_model=RepositoryEntryDetailResponse,
        tags=["repository"],
        operation_id="getRepositoryEntry",
    )
    def get_repository_entry(
        entry_id: str,
        principal: Principal = Depends(require_principal),
    ) -> RepositoryEntryDetailResponse:
        """Inspect one repository entry and its source versions."""
        _require_repository_enabled(state)
        entry = state.store.get_repository_entry(entry_id)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"repository entry not found: {entry_id}")
        project_for_request(principal, entry.project_id)
        if entry.status != "published":
            _require_repository_owner_or_admin(principal, entry)
        return _repository_entry_detail(state, entry)

    @app.post(
        "/repository/entries/{entry_id}/validate",
        response_model=RepositoryValidationResponse,
        tags=["repository"],
        operation_id="validateRepositoryEntry",
    )
    def validate_repository_entry(
        entry_id: str,
        request: RepositoryValidateRequest,
        version: int | None = None,
        principal: Principal = Depends(require_principal),
    ) -> RepositoryValidationResponse:
        """Validate a submitted repository version before review."""
        _require_repository_enabled(state)
        _forbid_viewer_write(principal)
        entry = state.store.get_repository_entry(entry_id)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"repository entry not found: {entry_id}")
        project_for_request(principal, entry.project_id)
        _require_repository_owner_or_admin(principal, entry)
        source_version = _repository_requested_version(state, entry, version)
        if entry.type == "notebook_function":
            record = state.store.get_notebook_goblin(source_version.kind)
            if record is None:
                raise HTTPException(
                    status_code=500,
                    detail=f"repository notebook goblin missing: {source_version.kind}",
                )
            input_payload = notebook_worker_input(record, request.input)
            timeout_seconds = request.timeout_seconds or record.timeout_seconds
            if _running_in_kubernetes():
                result = _validate_notebook_with_kubernetes(
                    record=record,
                    input_payload=input_payload,
                    require_success=request.require_success,
                    timeout_seconds=timeout_seconds,
                    redis_url=state.settings.redis_url,
                    event_bus=state.event_bus,
                )
            else:
                results = validate_workers(
                    registry=GoblinRegistry.from_definitions([notebook_definition(record)]),
                    workers=notebook_worker_map(record),
                    input_payload=input_payload,
                    kinds=[record.kind],
                    require_success=request.require_success,
                    prebuilt_image=True,
                    timeout_seconds=timeout_seconds,
                    redis_url=state.settings.redis_url,
                )
                result = results[0]
                result = result.model_copy(
                    update={
                        "image_digest": notebook_validation_identity(
                            result.image_digest,
                            record.source_hash,
                        )
                    }
                )
            state.store.save_worker_validation(validation_record(result))
            proof = result.model_dump(mode="json")
            outcome = "success" if result.ok else "failure"
            if not result.ok:
                audit(
                    state.store,
                    action="repository.validate",
                    outcome=outcome,
                    principal=principal,
                    project_id=entry.project_id,
                    resource_type="repository_entry",
                    resource_id=entry.id,
                    detail={"version": source_version.version, "proof": proof},
                )
                raise HTTPException(status_code=422, detail=proof)
        else:
            record = state.store.get_notebook_service(source_version.kind)
            if record is None:
                raise HTTPException(
                    status_code=500,
                    detail=f"repository notebook service missing: {source_version.kind}",
                )
            manager = _notebook_service_runtime_manager(state)
            try:
                runtime = manager.validate(
                    record,
                    timeout_seconds=request.timeout_seconds or 120,
                )
            except NotebookServiceRuntimeError as error:
                proof = {"ok": False, "error": str(error)}
                audit(
                    state.store,
                    action="repository.validate",
                    outcome="failure",
                    principal=principal,
                    project_id=entry.project_id,
                    resource_type="repository_entry",
                    resource_id=entry.id,
                    detail={"version": source_version.version, "proof": proof},
                )
                raise HTTPException(status_code=422, detail=proof) from error
            state.store.update_notebook_service_runtime(
                record.kind,
                runtime_status="validated",
                updated_at=utc_now(),
            )
            proof = {"ok": True, "runtime": runtime.model()}
            outcome = "success"

        updated_version = state.store.transition_repository_version_status(
            entry.id,
            source_version.version,
            "validated",
            updated_at=utc_now(),
            validation_proof=proof,
        )
        updated_entry = state.store.get_repository_entry(entry.id)
        assert updated_entry is not None
        audit(
            state.store,
            action="repository.validate",
            outcome=outcome,
            principal=principal,
            project_id=entry.project_id,
            resource_type="repository_entry",
            resource_id=entry.id,
            detail={"version": updated_version.version, "proof": proof},
        )
        return RepositoryValidationResponse(
            entry=updated_entry,
            version=updated_version,
            validation=proof,
        )

    @app.post(
        "/repository/entries/{entry_id}/request-review",
        response_model=RepositoryEntryDetailResponse,
        tags=["repository"],
        operation_id="requestRepositoryReview",
    )
    def request_repository_review(
        entry_id: str,
        request: RepositoryReviewRequest,
        version: int | None = None,
        principal: Principal = Depends(require_principal),
    ) -> RepositoryEntryDetailResponse:
        """Request admin review for a validated repository version."""
        _require_repository_enabled(state)
        entry = state.store.get_repository_entry(entry_id)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"repository entry not found: {entry_id}")
        project_for_request(principal, entry.project_id)
        _require_repository_owner_or_admin(principal, entry)
        source_version = _repository_requested_version(state, entry, version)
        if source_version.status != "validated":
            raise HTTPException(status_code=409, detail="repository version must be validated")
        updated = state.store.transition_repository_version_status(
            entry.id,
            source_version.version,
            "pending_review",
            updated_at=utc_now(),
        )
        audit(
            state.store,
            action="repository.review_requested",
            outcome="success",
            principal=principal,
            project_id=entry.project_id,
            resource_type="repository_entry",
            resource_id=entry.id,
            detail={"version": updated.version, "note": request.note},
        )
        refreshed = state.store.get_repository_entry(entry.id)
        assert refreshed is not None
        return _repository_entry_detail(state, refreshed)

    @app.post(
        "/repository/entries/{entry_id}/approve",
        response_model=RepositoryEntryDetailResponse,
        tags=["repository"],
        operation_id="approveRepositoryEntry",
    )
    def approve_repository_entry(
        entry_id: str,
        request: RepositoryReviewRequest,
        version: int | None = None,
        principal: Principal = Depends(require_admin_principal),
    ) -> RepositoryEntryDetailResponse:
        """Approve a pending repository version for publication."""
        _require_repository_enabled(state)
        entry = state.store.get_repository_entry(entry_id)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"repository entry not found: {entry_id}")
        project_for_request(principal, entry.project_id)
        source_version = _repository_requested_version(state, entry, version)
        if source_version.status != "pending_review":
            raise HTTPException(status_code=409, detail="repository version must be pending review")
        updated = state.store.transition_repository_version_status(
            entry.id,
            source_version.version,
            "approved",
            updated_at=utc_now(),
            approved_by=principal.user_id,
        )
        audit(
            state.store,
            action="repository.approve",
            outcome="success",
            principal=principal,
            project_id=entry.project_id,
            resource_type="repository_entry",
            resource_id=entry.id,
            detail={"version": updated.version, "note": request.note},
        )
        refreshed = state.store.get_repository_entry(entry.id)
        assert refreshed is not None
        return _repository_entry_detail(state, refreshed)

    @app.post(
        "/repository/entries/{entry_id}/reject",
        response_model=RepositoryEntryDetailResponse,
        tags=["repository"],
        operation_id="rejectRepositoryEntry",
    )
    def reject_repository_entry(
        entry_id: str,
        request: RepositoryReviewRequest,
        version: int | None = None,
        principal: Principal = Depends(require_admin_principal),
    ) -> RepositoryEntryDetailResponse:
        """Reject a repository version after review."""
        _require_repository_enabled(state)
        entry = state.store.get_repository_entry(entry_id)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"repository entry not found: {entry_id}")
        project_for_request(principal, entry.project_id)
        source_version = _repository_requested_version(state, entry, version)
        updated = state.store.transition_repository_version_status(
            entry.id,
            source_version.version,
            "rejected",
            updated_at=utc_now(),
        )
        audit(
            state.store,
            action="repository.reject",
            outcome="success",
            principal=principal,
            project_id=entry.project_id,
            resource_type="repository_entry",
            resource_id=entry.id,
            detail={"version": updated.version, "note": request.note},
        )
        refreshed = state.store.get_repository_entry(entry.id)
        assert refreshed is not None
        return _repository_entry_detail(state, refreshed)

    @app.post(
        "/repository/entries/{entry_id}/publish",
        response_model=RepositoryEntryDetailResponse,
        tags=["repository"],
        operation_id="publishRepositoryEntry",
    )
    def publish_repository_entry(
        entry_id: str,
        request: RepositoryPublishRequest,
        principal: Principal = Depends(require_admin_principal),
    ) -> RepositoryEntryDetailResponse:
        """Publish an approved repository version so users can discover it."""
        _require_repository_enabled(state)
        entry = state.store.get_repository_entry(entry_id)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"repository entry not found: {entry_id}")
        project_for_request(principal, entry.project_id)
        source_version = _repository_requested_version(state, entry, request.version)
        if source_version.status != "approved":
            raise HTTPException(status_code=409, detail="repository version must be approved")
        updated = state.store.transition_repository_version_status(
            entry.id,
            source_version.version,
            "published",
            updated_at=utc_now(),
            approved_by=source_version.approved_by,
            approved_at=source_version.approved_at,
        )
        audit(
            state.store,
            action="repository.publish",
            outcome="success",
            principal=principal,
            project_id=entry.project_id,
            resource_type="repository_entry",
            resource_id=entry.id,
            detail={"version": updated.version},
        )
        refreshed = state.store.get_repository_entry(entry.id)
        assert refreshed is not None
        return _repository_entry_detail(state, refreshed)

    @app.post(
        "/repository/entries/{entry_id}/retire",
        response_model=RepositoryEntryDetailResponse,
        tags=["repository"],
        operation_id="retireRepositoryEntry",
    )
    def retire_repository_entry(
        entry_id: str,
        request: RepositoryReviewRequest,
        principal: Principal = Depends(require_admin_principal),
    ) -> RepositoryEntryDetailResponse:
        """Retire a repository entry from normal discovery."""
        _require_repository_enabled(state)
        entry = state.store.get_repository_entry(entry_id)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"repository entry not found: {entry_id}")
        project_for_request(principal, entry.project_id)
        updated = state.store.transition_repository_entry_status(
            entry.id,
            "retired",
            updated_at=utc_now(),
        )
        audit(
            state.store,
            action="repository.retire",
            outcome="success",
            principal=principal,
            project_id=entry.project_id,
            resource_type="repository_entry",
            resource_id=entry.id,
            detail={"note": request.note},
        )
        return _repository_entry_detail(state, updated)

    @app.delete(
        "/repository/entries/{entry_id}",
        response_model=RepositoryDeleteResponse,
        tags=["repository"],
        operation_id="deleteRepositoryEntry",
    )
    def delete_repository_entry(
        entry_id: str,
        principal: Principal = Depends(require_admin_principal),
    ) -> RepositoryDeleteResponse:
        """Permanently delete a draft, rejected, or retired repository entry."""
        _require_repository_enabled(state)
        entry = state.store.get_repository_entry(entry_id)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"repository entry not found: {entry_id}")
        project_for_request(principal, entry.project_id)
        if entry.status not in {"draft", "rejected", "retired"}:
            raise HTTPException(
                status_code=409,
                detail=(
                    "repository entry must be draft, rejected, or retired before permanent "
                    "deletion; retire published entries first"
                ),
            )
        for version_record in state.store.list_repository_versions(entry.id):
            if entry.type != "notebook_service":
                continue
            record = state.store.get_notebook_service(version_record.kind)
            if record is not None and record.active_service_id:
                raise HTTPException(
                    status_code=409,
                    detail="repository service is still running; stop it before deleting",
                )
        try:
            deleted = state.store.delete_repository_entry(entry.id)
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        audit(
            state.store,
            action="repository.delete",
            outcome="success",
            principal=principal,
            project_id=entry.project_id,
            resource_type="repository_entry",
            resource_id=entry.id,
            detail={
                "name": entry.name,
                "status": entry.status,
                "deleted_versions": deleted["deleted_versions"],
                "deleted_notebook_records": deleted["deleted_notebook_records"],
            },
        )
        return RepositoryDeleteResponse(deleted=True, **deleted)

    @app.post(
        "/repository/functions/{name}/run",
        response_model=RepositoryFunctionRunResponse,
        tags=["repository"],
        operation_id="runRepositoryFunction",
    )
    def run_repository_function(
        name: str,
        request: RepositoryFunctionRunRequest,
        principal: Principal = Depends(require_principal),
    ) -> RepositoryFunctionRunResponse:
        """Queue an approved repository function goblin by project-local name."""
        entry, version_record = _resolve_published_repository_version(
            name=name,
            expected_type="notebook_function",
            principal=principal,
            project_id=request.project_id,
            version=request.version,
        )
        record = state.store.get_notebook_goblin(version_record.kind)
        if record is None:
            raise HTTPException(
                status_code=500,
                detail=f"repository notebook goblin missing: {version_record.kind}",
            )
        job = _queue_job(
            JobCreateRequest(
                kind=version_record.kind,
                input=request.input,
                project_id=entry.project_id,
                priority=request.priority,
                correlation_id=request.correlation_id,
                max_retries=request.max_retries,
                timeout_seconds=request.timeout_seconds,
            ),
            principal,
            repository_entry=entry,
            repository_version=version_record,
        )
        audit(
            state.store,
            action="repository.run",
            outcome="success",
            principal=principal,
            project_id=entry.project_id,
            resource_type="repository_entry",
            resource_id=entry.id,
            detail={
                "name": entry.name,
                "version": version_record.version,
                "kind": version_record.kind,
                "job_id": job.id,
            },
        )
        return RepositoryFunctionRunResponse(entry=entry, version=version_record, job=job)

    def _repository_service_record(
        entry: RepositoryEntryRecord,
        version_record: RepositoryVersionRecord,
    ) -> NotebookServiceRecord:
        record = state.store.get_notebook_service(version_record.kind)
        if record is None:
            raise HTTPException(
                status_code=500,
                detail=f"repository notebook service missing: {version_record.kind}",
            )
        if record.project_id != entry.project_id:
            raise HTTPException(
                status_code=500,
                detail=f"repository notebook service project mismatch: {version_record.kind}",
            )
        return record

    def _repository_proxy_query_string(raw_query: str) -> str:
        """Remove repository selector params before forwarding to the service."""
        filtered = [
            (key, value)
            for key, value in urlparse.parse_qsl(raw_query, keep_blank_values=True)
            if key not in {"project_id", "version"}
        ]
        return urlparse.urlencode(filtered, doseq=True)

    @app.post(
        "/repository/services/{name}/start",
        response_model=RepositoryServiceStartResponse,
        tags=["repository"],
        operation_id="startRepositoryService",
    )
    def start_repository_service(
        name: str,
        request: RepositoryServiceStartRequest,
        principal: Principal = Depends(require_principal),
    ) -> RepositoryServiceStartResponse:
        """Start an approved repository ASGI service by project-local name."""
        _forbid_viewer_write(principal)
        entry, version_record = _resolve_published_repository_version(
            name=name,
            expected_type="notebook_service",
            principal=principal,
            project_id=request.project_id,
            version=request.version,
        )
        record = _repository_service_record(entry, version_record)
        manager = _notebook_service_runtime_manager(state)
        _stop_existing_notebook_service_runtime(state, manager, record)
        try:
            runtime_proof = manager.start(record, timeout_seconds=request.timeout_seconds)
        except NotebookServiceRuntimeError as error:
            audit(
                state.store,
                action="repository.start",
                outcome="failure",
                principal=principal,
                project_id=record.project_id,
                resource_type="repository_entry",
                resource_id=entry.id,
                detail={"name": entry.name, "version": version_record.version, "error": str(error)},
            )
            raise HTTPException(status_code=422, detail=str(error)) from error

        service = LongServiceRecord(
            id=str(uuid4()),
            kind=record.kind,
            project_id=record.project_id,
            image=record.image,
            base_url=runtime_proof.base_url,
            probe_path=record.probe_path,
            status="running",
            created_at=utc_now(),
            created_by=principal.user_id,
            last_probe_json=runtime_proof.probe,
        )
        state.store.save_long_service(service)
        updated = state.store.update_notebook_service_runtime(
            record.kind,
            runtime_status="running",
            runtime_backend=runtime_proof.backend,
            runtime_name=runtime_proof.name,
            active_service_id=service.id,
            updated_at=utc_now(),
        )
        assert updated is not None
        probe = _probe_long_service_record(state, service)
        state.event_bus.emit(
            "repository.service.started",
            source="api",
            project_id=record.project_id,
            worker_id=service.id,
            payload={
                "name": entry.name,
                "version": version_record.version,
                "kind": record.kind,
                "backend": runtime_proof.backend,
            },
        )
        audit(
            state.store,
            action="repository.start",
            outcome="success",
            principal=principal,
            project_id=record.project_id,
            resource_type="repository_entry",
            resource_id=entry.id,
            detail={
                "name": entry.name,
                "version": version_record.version,
                "kind": record.kind,
                "service_id": service.id,
                "runtime": runtime_proof.model(),
            },
        )
        return RepositoryServiceStartResponse(
            entry=entry,
            version=version_record,
            notebook_service=updated,
            service=service,
            runtime=runtime_proof.model(),
            probe=probe,
        )

    def _active_repository_service(
        entry: RepositoryEntryRecord,
        version_record: RepositoryVersionRecord,
    ) -> tuple[NotebookServiceRecord, LongServiceRecord]:
        record = _repository_service_record(entry, version_record)
        if not record.active_service_id:
            raise HTTPException(
                status_code=409,
                detail=f"repository service is not running: {entry.name}",
            )
        service = state.store.get_long_service(record.active_service_id)
        if service is None:
            raise HTTPException(
                status_code=409,
                detail=f"repository service runtime is missing: {entry.name}",
            )
        return record, service

    @app.post(
        "/repository/services/{name}/probe",
        response_model=RepositoryServiceProbeResponse,
        tags=["repository"],
        operation_id="probeRepositoryService",
    )
    def probe_repository_service(
        name: str,
        request: RepositoryServiceProbeRequest,
        principal: Principal = Depends(require_principal),
    ) -> RepositoryServiceProbeResponse:
        """Probe a running approved repository ASGI service by name."""
        entry, version_record = _resolve_published_repository_version(
            name=name,
            expected_type="notebook_service",
            principal=principal,
            project_id=request.project_id,
            version=request.version,
        )
        record, service = _active_repository_service(entry, version_record)
        project_for_request(principal, service.project_id)
        if service.status == "stopped":
            raise HTTPException(status_code=409, detail=f"long service is stopped: {service.id}")
        probe = _probe_long_service_record(state, service)
        audit(
            state.store,
            action="repository.probe",
            outcome="success",
            principal=principal,
            project_id=service.project_id,
            resource_type="repository_entry",
            resource_id=entry.id,
            detail={
                "name": entry.name,
                "version": version_record.version,
                "kind": record.kind,
                "service_id": service.id,
                "url": probe.request["url"],
            },
        )
        return RepositoryServiceProbeResponse(
            entry=entry,
            version=version_record,
            notebook_service=record,
            probe=probe,
        )

    @app.post(
        "/repository/services/{name}/stop",
        response_model=RepositoryServiceStopResponse,
        tags=["repository"],
        operation_id="stopRepositoryService",
    )
    def stop_repository_service(
        name: str,
        request: RepositoryServiceStopRequest,
        principal: Principal = Depends(require_principal),
    ) -> RepositoryServiceStopResponse:
        """Stop a running approved repository ASGI service by name."""
        _forbid_viewer_write(principal)
        entry, version_record = _resolve_published_repository_version(
            name=name,
            expected_type="notebook_service",
            principal=principal,
            project_id=request.project_id,
            version=request.version,
        )
        record = _repository_service_record(entry, version_record)
        manager = _notebook_service_runtime_manager(state)
        runtime = manager.stop(record)
        service = None
        if record.active_service_id:
            service = state.store.update_long_service_status(
                record.active_service_id,
                status="stopped",
                last_probe_json={"stopped_by": "repository.stop"},
            )
        updated = state.store.update_notebook_service_runtime(
            record.kind,
            runtime_status="stopped",
            runtime_backend=None,
            runtime_name=None,
            active_service_id=None,
            updated_at=utc_now(),
        )
        assert updated is not None
        audit(
            state.store,
            action="repository.stop",
            outcome="success",
            principal=principal,
            project_id=record.project_id,
            resource_type="repository_entry",
            resource_id=entry.id,
            detail={
                "name": entry.name,
                "version": version_record.version,
                "kind": record.kind,
                "service_id": service.id if service else None,
                "runtime": runtime,
            },
        )
        return RepositoryServiceStopResponse(
            entry=entry,
            version=version_record,
            notebook_service=updated,
            service=service,
            runtime=runtime,
        )

    @app.api_route(
        "/repository/services/{name}/proxy",
        methods=SERVICE_PROXY_METHODS,
        tags=["repository"],
        operation_id="proxyRepositoryServiceRoot",
        include_in_schema=False,
    )
    @app.api_route(
        "/repository/services/{name}/proxy/{path:path}",
        methods=SERVICE_PROXY_METHODS,
        tags=["repository"],
        operation_id="proxyRepositoryService",
        include_in_schema=False,
    )
    async def proxy_repository_service(
        name: str,
        request: Request,
        path: str = "",
        project_id: str | None = None,
        version: int | None = Query(default=None, gt=0),
        principal: Principal = Depends(require_principal),
    ) -> FastAPIResponse:
        """Proxy authenticated HTTP traffic to an approved repository service."""
        entry, version_record = _resolve_published_repository_version(
            name=name,
            expected_type="notebook_service",
            principal=principal,
            project_id=project_id,
            version=version,
        )
        record, service = _active_repository_service(entry, version_record)
        project_for_request(principal, service.project_id)
        return await _proxy_long_service_request(
            service=service,
            request=request,
            path=path,
            principal=principal,
            action="repository.proxy",
            resource_type="repository_entry",
            resource_id=entry.id,
            detail_extra={
                "name": entry.name,
                "version": version_record.version,
                "kind": record.kind,
                "service_id": service.id,
            },
            query_string=_repository_proxy_query_string(request.url.query),
        )

    @app.post("/jobs", response_model=JobRecord, tags=["jobs"], operation_id="createJob")
    def create_job(
        request: JobCreateRequest,
        principal: Principal = Depends(require_principal),
    ) -> JobRecord:
        """Queue one job for later scheduler execution."""
        return _queue_job(request, principal)

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
        cancelled, changed = state.store.try_cancel_job(job_id)
        if cancelled is None:
            raise HTTPException(status_code=404, detail=f"job not found: {job_id}")
        if not changed:
            raise HTTPException(status_code=409, detail=f"job is terminal: {cancelled.status}")
        state.event_bus.emit(
            "job.cancelled",
            source="api",
            project_id=cancelled.project_id,
            job_id=cancelled.id,
            fanout_id=cancelled.fanout_id,
            schedule_id=cancelled.schedule_id,
            payload={"kind": cancelled.kind},
            after=cancelled.created_at,
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
        "/admin/runtime/jobs/{job_id}/kill",
        response_model=RuntimeTerminationResponse,
        tags=["admin"],
        operation_id="killJobRuntime",
    )
    def kill_job_runtime(
        job_id: str,
        request: RuntimeTerminationRequest,
        principal: Principal = Depends(require_admin_principal),
    ) -> RuntimeTerminationResponse:
        """Hard-kill runtime objects labeled for one Goblin King job."""
        job = state.store.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"job not found: {job_id}")
        project_for_request(principal, job.project_id)
        result = terminate_runtime(
            job_id=job_id,
            runtime=request.runtime,
            namespace=request.namespace,
        )
        cancelled = False
        if job.status not in TERMINAL_JOB_STATUSES:
            cancelled = state.store.cancel_job(job_id) is not None
        return record_runtime_termination(
            state,
            principal=principal,
            project_id=job.project_id,
            target_type="job",
            target_id=job_id,
            runtime=request.runtime,
            killed=result.killed,
            errors=result.errors,
            cancelled=cancelled,
        )

    @app.post(
        "/admin/runtime/runs/{run_id}/kill",
        response_model=RuntimeTerminationResponse,
        tags=["admin"],
        operation_id="killRunRuntime",
    )
    def kill_run_runtime(
        run_id: str,
        request: RuntimeTerminationRequest,
        principal: Principal = Depends(require_admin_principal),
    ) -> RuntimeTerminationResponse:
        """Hard-kill runtime objects labeled for one Goblin King run."""
        run = state.store.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"run not found: {run_id}")
        job = state.store.get_job(run.job_id)
        project_for_request(principal, job.project_id if job else run.project_id)
        result = terminate_runtime(
            run_id=run_id,
            runtime=request.runtime,
            namespace=request.namespace,
        )
        return record_runtime_termination(
            state,
            principal=principal,
            project_id=job.project_id if job else run.project_id,
            target_type="run",
            target_id=run_id,
            runtime=request.runtime,
            killed=result.killed,
            errors=result.errors,
            cancelled=False,
        )

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
                resource_policies=state.resource_policies,
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
                    after=job.created_at,
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
                resource_policies=state.resource_policies,
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
                after=retry.created_at,
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
        except ResourcePolicyError as error:
            source_project_id = source.project_id if "source" in locals() and source else None
            source_kind = source.kind if "source" in locals() and source else job_id
            record_policy_rejection(
                state,
                principal,
                source_project_id,
                source_kind,
                str(error),
            )
            raise HTTPException(status_code=422, detail=str(error)) from error
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
        schedule = schedule_from_request(state.registry, request).model_copy(
            update={"project_id": project_id}
        )
        try:
            effective_policy(
                state,
                schedule.kind,
                timeout_seconds=schedule.timeout_seconds,
                max_retries=schedule.max_retries,
            )
        except ResourcePolicyError as error:
            record_policy_rejection(state, principal, project_id, schedule.kind, str(error))
            raise HTTPException(status_code=422, detail=str(error)) from error
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
        validate_cron(update.get("cron", schedule.cron))
        validate_timezone(update.get("timezone", schedule.timezone))
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

    @app.get(
        "/events/stream/status",
        response_model=EventStreamStatusResponse,
        tags=["events"],
        operation_id="getEventStreamStatus",
    )
    def get_event_stream_status(
        stream: str = DEFAULT_EVENT_STREAM,
        _principal: Principal = Depends(require_principal),
    ) -> EventStreamStatusResponse:
        """Return Redis Stream delivery status for event transport."""
        return EventStreamStatusResponse.model_validate(
            stream_status(state.settings.redis_url, stream=stream)
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
                oidc=state.settings.oidc,
                jupyterhub=state.settings.jupyterhub,
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
        path = artifact_file_path(state.artifact_root, artifact)
        if path is None or not path.exists() or not path.is_file():
            raise HTTPException(status_code=404, detail=f"artifact file not found: {artifact_name}")
        return FileResponse(path, media_type=artifact.media_type, filename=artifact.name)

    return app
