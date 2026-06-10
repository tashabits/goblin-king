"""FastAPI control plane for Goblin King jobs, schedules, runs, and artifacts."""
# ruff: noqa: B008

from __future__ import annotations

import asyncio
import json
from typing import Any
from urllib import error as urlerror
from urllib import request as urlrequest
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, Security, WebSocket
from fastapi.responses import FileResponse, HTMLResponse
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
    PageMeta,
    ProjectCreateRequest,
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
    ProjectRecord,
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
from goblin_king.project import ProjectSettingsError
from goblin_king.registry import RegistryError
from goblin_king.resource_policies import ResourcePolicyError
from goblin_king.scheduler import next_run_after
from goblin_king.termination import terminate_runtime
from goblin_king.workers import WorkerConfigError

TERMINAL_JOB_STATUSES = {"completed", "failed", "timed_out", "cancelled"}
bearer_scheme = HTTPBearer(auto_error=False)


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
                    "source": "project-config"
                    if definition.kind in state._project_defined_kinds
                    else "registry",
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
            max_retries=(policy.max_retries or 0) if policy else request.max_retries,
            timeout_seconds=policy.timeout_seconds if policy else request.timeout_seconds,
            metadata=goblin_job_metadata(definition, policy),
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
