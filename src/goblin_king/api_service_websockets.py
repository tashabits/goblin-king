"""Authentication, authorization, and audit boundary for service WebSocket relays."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from fastapi import WebSocket

from goblin_king.api_state import AppState
from goblin_king.auth import (
    AuthError,
    Principal,
    RateLimitExceeded,
    audit,
    authenticate_token,
    check_rate_limit,
    require_project_access,
)
from goblin_king.contracts import LongServiceRecord
from goblin_king.service_websocket_drain import WebSocketDrainRegistry
from goblin_king.service_websocket_proxy import (
    ServiceWebSocketProxyConfig,
    ServiceWebSocketProxyError,
    ServiceWebSocketProxyResult,
    close_websocket,
    filtered_query_string,
    proxy_managed_service_websocket,
    service_websocket_url,
)


async def authenticate_service_websocket(
    state: AppState,
    websocket: WebSocket,
) -> Principal | None:
    """Authenticate a WebSocket using bearer or browser-compatible query token auth."""
    token = _websocket_token(websocket)
    if token is None:
        audit(
            state.store,
            action="auth.failure",
            outcome="denied",
            detail={"route": websocket.url.path, "reason": "missing token"},
        )
        await close_websocket(websocket, 1008, "missing or invalid bearer token")
        return None
    try:
        principal = authenticate_token(
            state.store,
            token,
            bootstrap_token=state.settings.bootstrap_admin_token,
            oidc=state.settings.oidc,
            jupyterhub=state.settings.jupyterhub,
        )
        check_rate_limit(
            state.store,
            principal=principal,
            route=websocket.url.path,
            max_per_minute=state.settings.rate_limit_per_minute,
        )
        return principal
    except AuthError as error:
        audit(
            state.store,
            action="auth.failure",
            outcome="denied",
            detail={"route": websocket.url.path, "reason": str(error)},
        )
        await close_websocket(websocket, 1008, "missing or invalid bearer token")
    except RateLimitExceeded as error:
        audit(
            state.store,
            action="auth.failure",
            outcome="rate_limited",
            detail={"route": websocket.url.path, "reason": str(error)},
        )
        await close_websocket(websocket, 1013, "rate limit exceeded")
    return None


async def authorize_service_websocket(
    state: AppState,
    websocket: WebSocket,
    principal: Principal,
    project_id: str | None,
) -> bool:
    """Apply the same project boundary used by authenticated HTTP service proxying."""
    try:
        require_project_access(principal, project_id)
        return True
    except AuthError as error:
        audit(
            state.store,
            action="project.access_denied",
            outcome="denied",
            principal=principal,
            project_id=project_id,
            detail={"route": websocket.url.path, "reason": str(error)},
        )
        await close_websocket(websocket, 1008, "project access denied")
        return False


def service_websocket_ready(service: LongServiceRecord) -> bool:
    """Require a current registered route with persisted successful readiness proof."""
    if service.status != "running" or service.last_probe_at is None:
        return False
    proof = service.last_probe_json or {}
    status_code = proof.get("status_code")
    return isinstance(status_code, int) and 200 <= status_code < 300


async def proxy_service_websocket(
    state: AppState,
    drains: WebSocketDrainRegistry,
    websocket: WebSocket,
    *,
    service: LongServiceRecord,
    path: str,
    principal: Principal,
    action: str,
    resource_type: str,
    resource_id: str,
    query_excluded: Iterable[str],
    detail_extra: dict[str, Any] | None = None,
) -> None:
    """Resolve one ready route, relay bounded frames, and persist its outcome."""
    detail = {
        "path": path,
        "service_id": service.id,
        "kind": service.kind,
        **(detail_extra or {}),
    }
    if not service_websocket_ready(service):
        audit(
            state.store,
            action=action,
            outcome="denied",
            principal=principal,
            project_id=service.project_id,
            resource_type=resource_type,
            resource_id=resource_id,
            detail={**detail, "reason": "service is not ready"},
        )
        await close_websocket(websocket, 1013, "managed service is not ready")
        return
    if not await drains.register(service.id, websocket):
        audit(
            state.store,
            action=action,
            outcome="draining",
            principal=principal,
            project_id=service.project_id,
            resource_type=resource_type,
            resource_id=resource_id,
            detail={**detail, "reason": "service route is draining"},
        )
        await close_websocket(websocket, 1012, "managed service is being replaced")
        return

    settings = state.settings.service_websocket_proxy
    config = ServiceWebSocketProxyConfig(**settings.model_dump())
    query = filtered_query_string(websocket.url.query, excluded=query_excluded)

    def record_result(result: ServiceWebSocketProxyResult) -> None:
        outcome = (
            "success"
            if result.outcome in {"client_closed", "upstream_closed"}
            and result.close_code in {1000, 1001}
            else result.outcome
        )
        result_detail = {
            **detail,
            "terminal": result.outcome,
            "close_code": result.close_code,
            "reason": result.reason,
            "client_frames": result.client_frames,
            "client_bytes": result.client_bytes,
            "upstream_frames": result.upstream_frames,
            "upstream_bytes": result.upstream_bytes,
        }
        _record_proxy_outcome(
            state,
            action=action,
            outcome=outcome,
            principal=principal,
            service=service,
            resource_type=resource_type,
            resource_id=resource_id,
            detail=result_detail,
        )

    try:
        upstream_url = service_websocket_url(service.base_url, path, query)
        await proxy_managed_service_websocket(
            websocket,
            upstream_url=upstream_url,
            config=config,
            on_result=record_result,
        )
    except (ServiceWebSocketProxyError, ValueError) as error:
        close_code = error.close_code if isinstance(error, ServiceWebSocketProxyError) else 1008
        reason = error.reason if isinstance(error, ServiceWebSocketProxyError) else "invalid route"
        _record_proxy_outcome(
            state,
            action=action,
            outcome="failed",
            principal=principal,
            service=service,
            resource_type=resource_type,
            resource_id=resource_id,
            detail={**detail, "close_code": close_code, "reason": reason, "error": str(error)},
        )
        await close_websocket(websocket, close_code, reason)
    finally:
        await drains.unregister(service.id, websocket)


def _record_proxy_outcome(
    state: AppState,
    *,
    action: str,
    outcome: str,
    principal: Principal,
    service: LongServiceRecord,
    resource_type: str,
    resource_id: str,
    detail: dict[str, Any],
) -> None:
    audit(
        state.store,
        action=action,
        outcome=outcome,
        principal=principal,
        project_id=service.project_id,
        resource_type=resource_type,
        resource_id=resource_id,
        detail=detail,
    )
    state.event_bus.emit(
        "service.websocket_proxy",
        source="api",
        project_id=service.project_id,
        worker_id=service.id,
        payload={
            "kind": service.kind,
            "outcome": outcome,
            "close_code": detail.get("close_code"),
            "reason": detail.get("reason"),
        },
    )


def _websocket_token(websocket: WebSocket) -> str | None:
    authorization = websocket.headers.get("authorization")
    if authorization is not None:
        scheme, separator, value = authorization.partition(" ")
        if separator and scheme.lower() == "bearer" and value.strip():
            return value.strip()
        return None
    token = websocket.query_params.get("token")
    return token if token else None
