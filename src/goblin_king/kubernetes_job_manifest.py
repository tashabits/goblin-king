"""Manifest construction for finite Kubernetes worker Jobs."""

from __future__ import annotations

from typing import Any

from goblin_king.contracts import GoblinContext
from goblin_king.events import DEFAULT_HEARTBEAT_CHANNEL, worker_heartbeat_key
from goblin_king.kubernetes_artifact_config import (
    ARTIFACT_DESTINATION_ROOT_ENV,
    ARTIFACT_MAX_BYTES_ENV,
    ARTIFACT_MAX_FILES_ENV,
    ARTIFACT_PROJECT_ID_ENV,
    ARTIFACT_SOURCE_ROOT,
    ARTIFACT_SOURCE_ROOT_ENV,
    ARTIFACT_URI_ROOT_ENV,
    DEFAULT_ARTIFACT_MAX_BYTES,
    DEFAULT_ARTIFACT_MAX_FILES,
)
from goblin_king.kubernetes_placement import apply_kubernetes_placement, placement_metadata
from goblin_king.kubernetes_runtime_settings import KubernetesRuntimeSettings
from goblin_king.kubernetes_workload_security import apply_restricted_workload_security
from goblin_king.resource_policies import ResourcePolicy
from goblin_king.runtime_helpers import kubernetes_policy_fields, resource_policy_env
from goblin_king.versions import GOBLIN_CONTAINER_CONTRACT_VERSION


def build_kubernetes_job_manifest(
    *,
    name: str,
    config_name: str,
    image: str,
    context: GoblinContext,
    worker_id: str,
    timeout_seconds: int | None,
    settings: KubernetesRuntimeSettings,
    redis_url: str,
    heartbeat_interval_seconds: int,
    resource_policy: ResourcePolicy | None = None,
    placement: dict[str, dict[str, str]] | None = None,
    kind: str | None = None,
) -> dict[str, Any]:
    """Build a Job manifest without widening the typed runtime settings boundary."""
    worker_container = _worker_container(
        image=image,
        context=context,
        worker_id=worker_id,
        settings=settings,
        redis_url=redis_url,
        heartbeat_interval_seconds=heartbeat_interval_seconds,
        resource_policy=resource_policy,
    )
    result_forwarder_container = _result_forwarder_container(
        context=context,
        timeout_seconds=timeout_seconds,
        settings=settings,
        redis_url=redis_url,
        resource_policy=resource_policy,
    )
    pod_spec: dict[str, Any] = {
        "restartPolicy": "Never",
        "containers": [
            worker_container,
            result_forwarder_container,
        ],
        "volumes": [
            {"name": "input", "configMap": {"name": config_name}},
            {"name": "result", "emptyDir": {}},
            {"name": "artifacts", "emptyDir": {}},
        ],
    }
    if settings.artifact_retention is not None:
        pod_spec["volumes"].append(
            {
                "name": "retained-artifacts",
                "persistentVolumeClaim": {
                    "claimName": settings.artifact_retention.claim_name,
                },
            }
        )
    if settings.workload_image_pull_secret_names:
        pod_spec["imagePullSecrets"] = [
            {"name": secret_name}
            for secret_name in settings.workload_image_pull_secret_names
        ]
    if settings.workload_security_profile == "restricted-v1":
        effective_kind = kind or str(context.metadata.get("kind") or "") or None
        apply_restricted_workload_security(
            pod_spec=pod_spec,
            worker_container=worker_container,
            result_forwarder_container=result_forwarder_container,
            settings=settings.restricted_workload,
            kind=effective_kind,
            resource_policy_read_only_root=(
                resource_policy.filesystem.read_only_root if resource_policy else None
            ),
        )
    effective_placement = placement or placement_metadata(None, context)
    if effective_placement is not None:
        apply_kubernetes_placement(pod_spec, effective_placement)

    labels = {
        "goblin-king.worker": "true",
        "goblin-king.run-id": context.run_id,
        "goblin-king.job-id": str(context.metadata.get("job_id", "")),
    }
    spec: dict[str, Any] = {
        "backoffLimit": 0,
        "template": {"metadata": {"labels": labels}, "spec": pod_spec},
    }
    if timeout_seconds is not None:
        spec["activeDeadlineSeconds"] = timeout_seconds
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {"name": name, "labels": labels},
        "spec": spec,
    }


def _worker_container(
    *,
    image: str,
    context: GoblinContext,
    worker_id: str,
    settings: KubernetesRuntimeSettings,
    redis_url: str,
    heartbeat_interval_seconds: int,
    resource_policy: ResourcePolicy | None,
) -> dict[str, Any]:
    container: dict[str, Any] = {
        "name": "worker",
        "image": image,
        "imagePullPolicy": settings.worker_image_pull_policy,
        "env": [
            {"name": "GOBLIN_CONTRACT_VERSION", "value": GOBLIN_CONTAINER_CONTRACT_VERSION},
            {"name": "GOBLIN_RUN_ID", "value": context.run_id},
            {"name": "GOBLIN_JOB_ID", "value": str(context.metadata.get("job_id", ""))},
            {"name": "GOBLIN_WORKER_ID", "value": worker_id},
            {"name": "GOBLIN_INPUT_PATH", "value": "/goblin-config/input.json"},
            {"name": "GOBLIN_CONTEXT_PATH", "value": "/goblin-config/context.json"},
            {"name": "GOBLIN_RESULT_PATH", "value": "/goblin-result/result.json"},
            {"name": "GOBLIN_ARTIFACT_ROOT", "value": "/artifacts"},
            {"name": "GOBLIN_REDIS_URL", "value": redis_url},
            {"name": "GOBLIN_HEARTBEAT_REDIS_URL", "value": redis_url},
            {"name": "GOBLIN_HEARTBEAT_CHANNEL", "value": DEFAULT_HEARTBEAT_CHANNEL},
            {"name": "GOBLIN_HEARTBEAT_KEY", "value": worker_heartbeat_key(context.run_id)},
            {
                "name": "GOBLIN_HEARTBEAT_INTERVAL_SECONDS",
                "value": str(heartbeat_interval_seconds),
            },
        ],
        "volumeMounts": [
            {"name": "input", "mountPath": "/goblin-config", "readOnly": True},
            {"name": "result", "mountPath": "/goblin-result"},
            {"name": "artifacts", "mountPath": "/artifacts"},
        ],
    }
    if resource_policy is not None:
        container["env"].append(
            {
                "name": "GOBLIN_EFFECTIVE_RESOURCE_POLICY_JSON",
                "value": resource_policy_env(resource_policy),
            }
        )
        container.update(kubernetes_policy_fields(resource_policy))
    return container


def _result_forwarder_container(
    *,
    context: GoblinContext,
    timeout_seconds: int | None,
    settings: KubernetesRuntimeSettings,
    redis_url: str,
    resource_policy: ResourcePolicy | None,
) -> dict[str, Any]:
    max_files = DEFAULT_ARTIFACT_MAX_FILES
    max_bytes = DEFAULT_ARTIFACT_MAX_BYTES
    if resource_policy is not None:
        if resource_policy.filesystem.artifact_max_files is not None:
            max_files = resource_policy.filesystem.artifact_max_files
        if resource_policy.filesystem.artifact_max_bytes is not None:
            max_bytes = resource_policy.filesystem.artifact_max_bytes
    container: dict[str, Any] = {
        "name": "result-forwarder",
        "image": settings.result_forwarder_image,
        "imagePullPolicy": settings.result_forwarder_image_pull_policy,
        "command": ["python", "-m", "goblin_king.kubernetes_result_forwarder"],
        "env": [
            {"name": "GOBLIN_RUN_ID", "value": context.run_id},
            {"name": "GOBLIN_REDIS_URL", "value": redis_url},
            {"name": "GOBLIN_RESULT_PATH", "value": "/goblin-result/result.json"},
            {
                "name": "GOBLIN_RESULT_WAIT_SECONDS",
                "value": str((timeout_seconds or 300) + 15),
            },
            {"name": ARTIFACT_SOURCE_ROOT_ENV, "value": ARTIFACT_SOURCE_ROOT},
            {
                "name": ARTIFACT_PROJECT_ID_ENV,
                "value": str(context.metadata.get("project_id") or ""),
            },
            {"name": ARTIFACT_MAX_FILES_ENV, "value": str(max_files)},
            {"name": ARTIFACT_MAX_BYTES_ENV, "value": str(max_bytes)},
        ],
        "volumeMounts": [
            {"name": "result", "mountPath": "/goblin-result"},
            {"name": "artifacts", "mountPath": ARTIFACT_SOURCE_ROOT, "readOnly": True},
        ],
    }
    if settings.artifact_retention is not None:
        container["env"].extend(
            [
                {
                    "name": ARTIFACT_DESTINATION_ROOT_ENV,
                    "value": settings.artifact_retention.destination_root,
                },
                {
                    "name": ARTIFACT_URI_ROOT_ENV,
                    "value": settings.artifact_retention.uri_root,
                },
            ]
        )
        container["volumeMounts"].append(
            {
                "name": "retained-artifacts",
                "mountPath": settings.artifact_retention.volume_mount_path,
                "subPath": settings.artifact_retention.volume_subdirectory,
            }
        )
    return container
