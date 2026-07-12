"""Contract validation for generic registry workers through Kubernetes Jobs."""

from __future__ import annotations

from typing import Any

from goblin_king.contracts import GoblinDefinition, utc_now
from goblin_king.events import EventBus
from goblin_king.registry import GoblinRegistry
from goblin_king.runtime import KubernetesRuntime, new_run_context
from goblin_king.validation import (
    WorkerValidationResult,
    kubernetes_image_identity,
    validation_job_id,
)
from goblin_king.workers import WorkerConfigError, WorkerImageMap


def validate_workers_with_kubernetes(
    *,
    registry: GoblinRegistry,
    workers: WorkerImageMap,
    input_payload: dict[str, Any],
    kinds: list[str] | None = None,
    require_success: bool = False,
    timeout_seconds: int = 120,
    redis_url: str = "redis://localhost:6379/0",
    event_bus: EventBus | None = None,
    runtime: KubernetesRuntime | None = None,
) -> list[WorkerValidationResult]:
    """Run selected generic workers as bounded Jobs and return contract proof details."""
    definitions, results = _selected_definitions(registry, kinds)
    active_runtime = runtime or KubernetesRuntime(
        workers=workers,
        redis_url=redis_url,
        event_bus=event_bus,
    )
    for definition in definitions:
        results.append(
            _validate_one_with_kubernetes(
                runtime=active_runtime,
                workers=workers,
                definition=definition,
                input_payload=input_payload,
                require_success=require_success,
                timeout_seconds=timeout_seconds,
            )
        )
    return results


def _selected_definitions(
    registry: GoblinRegistry,
    kinds: list[str] | None,
) -> tuple[list[GoblinDefinition], list[WorkerValidationResult]]:
    """Select requested registry definitions and report unknown kinds consistently."""
    definitions = registry.list()
    if not kinds:
        return definitions, []
    requested = set(kinds)
    selected = [definition for definition in definitions if definition.kind in requested]
    missing = sorted(requested - {definition.kind for definition in selected})
    return selected, [
        WorkerValidationResult(
            kind=kind,
            ok=False,
            validated_at=utc_now(),
            error=f"unknown goblin kind: {kind}",
        )
        for kind in missing
    ]


def _validate_one_with_kubernetes(
    *,
    runtime: KubernetesRuntime,
    workers: WorkerImageMap,
    definition: GoblinDefinition,
    input_payload: dict[str, Any],
    require_success: bool,
    timeout_seconds: int,
) -> WorkerValidationResult:
    """Validate one configured image with the same identity used by the scheduler gate."""
    checks: list[str] = []
    try:
        worker = workers.get(definition.kind)
    except WorkerConfigError as error:
        return WorkerValidationResult(
            kind=definition.kind,
            ok=False,
            validated_at=utc_now(),
            error=str(error),
        )

    image_identity = kubernetes_image_identity(worker.image)
    context = new_run_context(validation_job_id(definition.kind), definition.kind)
    try:
        observation = runtime.run_observed(
            definition,
            None,
            input_payload,
            context,
            timeout_seconds=timeout_seconds,
        )
    except Exception as error:  # cluster clients and test adapters may raise directly
        return WorkerValidationResult(
            kind=definition.kind,
            ok=False,
            image=worker.image,
            image_digest=image_identity,
            validated_at=utc_now(),
            error=f"Kubernetes validation failed: {error}",
            checks=checks,
        )

    checks.append("kubernetes-job")
    result = observation.result
    common = {
        "kind": definition.kind,
        "image": worker.image,
        "image_digest": image_identity,
        "validated_at": utc_now(),
        "result_status": result.status,
        "artifact_count": len(result.artifacts),
        "artifacts": result.artifacts,
        "checks": checks,
        "exit_code": observation.exit_code,
        "logs": observation.logs,
    }
    if not observation.result_envelope_valid:
        return WorkerValidationResult(
            ok=False,
            error=result.error or "worker did not produce a valid result envelope",
            **common,
        )

    checks.append("result-envelope")
    if result.metrics:
        checks.append("metrics")
    if result.handoff:
        checks.append("handoff")
    checks.append("artifact-metadata")
    if require_success and result.status != "success":
        return WorkerValidationResult(
            ok=False,
            error=result.error or "worker returned failed status",
            **common,
        )
    return WorkerValidationResult(ok=True, **common)
