"""Reattach scheduler attempts to retained pipeline Jobs using stable identities."""

import hashlib
import json
from uuid import NAMESPACE_URL, uuid5

from goblin_king.contracts import GoblinResult
from goblin_king.kubernetes_pod_diagnostics import KubernetesRunObservation
from goblin_king.pipeline_faults import configured_gate
from goblin_king.pipeline_kubernetes import RecoveryHold, RetainedExecution
from goblin_king.runtime_helpers import kubernetes_clients, kubernetes_name


def inspected_result(observation):
    """Only a complete trusted envelope distinguishes application failure from interruption."""
    logs = [value for key, value in observation["logs"].items() if key.endswith("/worker")]
    if not observation["succeeded"] or len(logs) != 1:
        return GoblinResult.failed(
            error="pipeline execution did not finish successfully",
            data={"execution": observation, "recovery_hold": True},
        )
    try:
        return GoblinResult.model_validate_json(logs[0].strip().splitlines()[-1])
    except (ValueError, IndexError):
        return GoblinResult.failed(
            error="pipeline execution omitted its durable result envelope",
            data={"recovery_hold": True},
        )


def run_retained_worker(runtime, definition, payload, context, timeout_seconds, resource_policy):
    """Retain Jobs and results until the controller acknowledges their authoritative evidence."""
    if not context.metadata.get("job_id") or resource_policy is None:
        return KubernetesRunObservation(
            result=GoblinResult.failed(
                error="pipeline execution requires a stable scheduler job and resource policy"
            )
        )
    worker = runtime.workers.get(definition.kind)
    pipeline = context.metadata["pipeline"]
    if (
        worker.image != pipeline["image"]
        or runtime.settings.workload_security_profile != pipeline["profile"]
    ):
        return KubernetesRunObservation(
            result=GoblinResult.failed(error="pipeline worker binding changed")
        )
    stable_id = str(uuid5(NAMESPACE_URL, "goblin-king/execution/" + context.metadata["job_id"]))
    stable = context.model_copy(
        update={
            "run_id": stable_id,
            "artifact_root": "/artifacts",
            "metadata": {
                key: value
                for key, value in context.metadata.items()
                if key not in {"attempt", "started_at"}
            },
        }
    )
    identity = {
        "job_id": context.metadata["job_id"],
        "kind": definition.kind,
        "input_sha256": hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest(),
        "worker": runtime.settings.validation_image_identity(worker.image, definition.kind),
        "policy": resource_policy.compact(),
    }
    batch, core = kubernetes_clients()
    config = json.loads(
        core.read_namespaced_config_map("pipeline-worker-config", runtime.namespace).data[
            "config.json"
        ]
    )
    execution = RetainedExecution(
        core,
        batch,
        runtime.namespace,
        stable_id,
        identity,
        fault_hook=configured_gate(core, runtime.namespace, config),
    )
    if execution.document["result"] is not None:
        return KubernetesRunObservation(
            result=GoblinResult.model_validate(execution.document["result"]),
            result_received=True,
            result_envelope_valid=True,
            job_created=True,
        )
    if definition.kind == "pipeline.build" and not execution.document["stages"]:
        source_namespace = config["sources"][payload["request"]["source"]["repository"]][
            "namespace"
        ]
        previous_namespace = execution.document.get("source_namespace")
        if previous_namespace and previous_namespace != source_namespace:
            raise RecoveryHold("source execution namespace changed before submission")
        execution.document["source_namespace"] = source_namespace
        execution.save()
    name = kubernetes_name(f"gk-{definition.kind}-{stable_id}")
    config_name = f"{name}-input"
    from kubernetes.client import ApiException

    try:
        core.create_namespaced_config_map(
            runtime.namespace,
            {
                "apiVersion": "v1",
                "kind": "ConfigMap",
                "metadata": {"name": config_name},
                "data": {
                    "input.json": json.dumps(payload),
                    "context.json": stable.model_dump_json(),
                },
            },
        )
    except ApiException as error:
        if error.status != 409:
            raise
        old = core.read_namespaced_config_map(config_name, runtime.namespace)
        if json.loads(old.data["input.json"]) != payload or json.loads(
            old.data["context.json"]
        ) != stable.model_dump(mode="json"):
            raise RecoveryHold("retained scheduler input conflicts") from None
    manifest = runtime._job_manifest(
        name=name,
        config_name=config_name,
        image=worker.image,
        context=stable,
        worker_id=f"k8s-worker-{stable_id}",
        timeout_seconds=timeout_seconds,
        resource_policy=resource_policy,
        kind=definition.kind,
    )
    observation = execution.job("worker", manifest, timeout=timeout_seconds or 2700)
    # Only the centrally pinned trusted worker's terminal result is parsed here.
    # Application/build output never enters this result channel directly.
    result = inspected_result(observation)
    result = result.model_copy(
        update={
            "data": {
                **result.data,
                "execution": {
                    "name": execution.name,
                    "namespace": runtime.namespace,
                    "job_uid": observation["job_uid"],
                    "pods": observation["pods"],
                },
            }
        }
    )
    execution.finish(result.model_dump(mode="json"))
    return KubernetesRunObservation(
        result=result, result_received=True, result_envelope_valid=True, job_created=True
    )
