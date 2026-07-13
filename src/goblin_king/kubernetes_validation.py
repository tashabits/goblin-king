"""Contract validation for generic registry workers through Kubernetes Jobs."""

from __future__ import annotations

from typing import Any

from goblin_king.contracts import GoblinContext, GoblinDefinition, utc_now
from goblin_king.events import EventBus
from goblin_king.kubernetes_artifact_config import ArtifactRetentionError
from goblin_king.kubernetes_artifacts import cleanup_retained_run
from goblin_king.kubernetes_runtime import KubernetesRuntime
from goblin_king.kubernetes_runtime_factory import build_kubernetes_runtime
from goblin_king.kubernetes_runtime_settings import KubernetesRuntimeSettings
from goblin_king.registry import GoblinRegistry
from goblin_king.runtime import new_run_context
from goblin_king.validation import (
    WorkerValidationResult,
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
    kubernetes_runtime_settings: KubernetesRuntimeSettings | None = None,
) -> list[WorkerValidationResult]:
    """Run selected generic workers as bounded Jobs and return contract proof details."""
    definitions, results = _selected_definitions(registry, kinds)
    runtime_settings = kubernetes_runtime_settings or KubernetesRuntimeSettings()
    active_runtime = build_kubernetes_runtime(
        workers=workers,
        redis_url=redis_url,
        event_bus=event_bus,
        settings=runtime_settings,
    )
    for definition in definitions:
        results.append(
            _validate_one_with_kubernetes(
                runtime=active_runtime,
                runtime_settings=runtime_settings,
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
    runtime_settings: KubernetesRuntimeSettings,
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

    image_identity = runtime_settings.validation_image_identity(
        worker.image,
        definition.kind,
    )
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
        validation = WorkerValidationResult(
            kind=definition.kind,
            ok=False,
            image=worker.image,
            image_digest=image_identity,
            validated_at=utc_now(),
            error=f"Kubernetes validation failed: {error}",
            checks=checks,
        )
        return with_kubernetes_validation_cleanup(validation, runtime_settings, context)

    if observation.job_created:
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
        validation = WorkerValidationResult(
            ok=False,
            error=result.error or "worker did not produce a valid result envelope",
            **common,
        )
    else:
        checks.append("result-envelope")
        if result.metrics:
            checks.append("metrics")
        if result.handoff:
            checks.append("handoff")
        checks.append("artifact-metadata")
        if require_success and result.status != "success":
            validation = WorkerValidationResult(
                ok=False,
                error=result.error or "worker returned failed status",
                **common,
            )
        else:
            validation = WorkerValidationResult(ok=True, **common)
    return with_kubernetes_validation_cleanup(validation, runtime_settings, context)


def cleanup_kubernetes_validation_run(
    settings: KubernetesRuntimeSettings,
    context: GoblinContext,
) -> str | None:
    """Remove retained bytes for a validation-only Run that has no durable Run owner."""
    retention = settings.artifact_retention
    if retention is None:
        return None
    raw_project_id = context.metadata.get("project_id")
    project_id = str(raw_project_id) if raw_project_id else None
    try:
        cleanup_retained_run(retention.uri_root, project_id, context.run_id)
    except (ArtifactRetentionError, OSError) as error:
        return f"Kubernetes validation artifact cleanup failed: {error}"
    return None


def with_kubernetes_validation_cleanup(
    validation: WorkerValidationResult,
    settings: KubernetesRuntimeSettings,
    context: GoblinContext,
) -> WorkerValidationResult:
    """Fail validation visibly when its retained bytes cannot be removed."""
    cleanup_error = cleanup_kubernetes_validation_run(settings, context)
    if cleanup_error is None:
        return validation
    prior_error = f"{validation.error}; " if validation.error else ""
    return validation.model_copy(
        update={
            "ok": False,
            "error": f"{prior_error}{cleanup_error}",
        }
    )
