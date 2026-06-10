"""API helpers for runtime termination and resource-policy bookkeeping."""

from __future__ import annotations

from typing import TYPE_CHECKING

from goblin_king.api_models import RuntimeTerminationResponse
from goblin_king.auth import Principal, audit
from goblin_king.termination import RuntimeTarget

if TYPE_CHECKING:
    from goblin_king.api_state import AppState


def record_runtime_termination(
    state: AppState,
    *,
    principal: Principal,
    project_id: str | None,
    target_type: str,
    target_id: str,
    runtime: RuntimeTarget,
    killed: list[str],
    errors: list[str],
    cancelled: bool,
) -> RuntimeTerminationResponse:
    """Persist audit/event proof for one scoped hard runtime termination attempt."""
    payload = {
        "target_type": target_type,
        "target_id": target_id,
        "runtime": runtime,
        "killed": killed,
        "errors": errors,
        "cancelled": cancelled,
    }
    state.event_bus.emit(
        "runtime.terminated",
        source="api",
        project_id=project_id,
        payload=payload,
    )
    audit(
        state.store,
        action="runtime.terminated",
        outcome="success" if not errors else "partial",
        principal=principal,
        project_id=project_id,
        resource_type=target_type,
        resource_id=target_id,
        detail=payload,
    )
    return RuntimeTerminationResponse(**payload)


def effective_policy(
    state: AppState,
    kind: str,
    *,
    timeout_seconds: int | None,
    max_retries: int | None,
):
    """Resolve one effective resource policy when policy enforcement is configured."""
    if state.resource_policies is None:
        return None
    return state.resource_policies.effective_for(
        kind,
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
    )


def record_policy_rejection(
    state: AppState,
    principal: Principal,
    project_id: str | None,
    kind: str,
    error: str,
) -> None:
    """Persist audit/event proof for a queue-time policy rejection."""
    state.event_bus.emit(
        "resource_policy.rejected",
        source="api",
        project_id=project_id,
        payload={"kind": kind, "error": error},
    )
    audit(
        state.store,
        action="resource_policy.rejected",
        outcome="failure",
        principal=principal,
        project_id=project_id,
        resource_type="resource_policy",
        detail={"kind": kind, "error": error},
    )
