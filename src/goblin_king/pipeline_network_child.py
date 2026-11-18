"""Retained networking phases inside one exclusively owned application release."""

import json
import re
from uuid import NAMESPACE_URL, uuid5

from kubernetes.client.exceptions import ApiException

from goblin_king.pipeline_kubernetes import RecoveryHold
from goblin_king.pipeline_runtime import inspected_result


def phase(
    ledger,
    core,
    *,
    payload,
    installed,
    stage,
    active,
    previous,
    prepare_only=False,
    probe_only=False,
):
    network = payload["networking"]
    image = network["image"]
    if image != installed.get("network_image") or not re.fullmatch(
        r"[^\s@]+@sha256:[a-f0-9]{64}", image
    ):
        raise RecoveryHold("networking phase image differs from installed approval")
    configuration = json.loads(json.dumps(network["configuration"]))
    if prepare_only and not (payload.get("previous") or {}).get("active"):
        configuration["ingress"]["enabled"] = False
    request = {
        "operation_id": payload["operation_id"],
        "release_key": payload["release_key"],
        "project_id": network["project_id"],
        "environment": payload["environment"],
        "configuration": configuration,
        "policy_revision": network["policy_revision"],
        "active": active,
        "port": payload["values"]["port"],
        "health_path": payload["values"]["healthPath"],
        "previous": previous,
        "parent_stage": stage,
        "parent_ledger": ledger.name,
        "prepare_only": prepare_only,
        "probe_only": probe_only,
    }
    requests = ledger.document.setdefault("network_requests", {})
    if stage in requests and requests[stage] != request:
        raise RecoveryHold("networking phase differs from retained intent")
    requests[stage] = request
    ledger.save()
    identity = str(uuid5(NAMESPACE_URL, ledger.name + "/" + stage))
    name = "network-" + identity
    namespace = payload["environment"]["namespace"]
    config_name = name + "-input"
    try:
        core.create_namespaced_config_map(
            namespace,
            {
                "apiVersion": "v1",
                "kind": "ConfigMap",
                "metadata": {
                    "name": config_name,
                    "labels": {"goblin-king.io/operation": payload["operation_id"]},
                },
                "data": {"input.json": json.dumps(request, sort_keys=True)},
            },
        )
    except ApiException as error:
        if error.status != 409:
            raise
        recorded = core.read_namespaced_config_map(config_name, namespace)
        if json.loads(recorded.data["input.json"]) != request:
            raise RecoveryHold(
                "network phase input conflicts with retained configuration"
            ) from None
    manifest = {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "labels": {"goblin-king.io/operation": payload["operation_id"]},
        },
        "spec": {
            "backoffLimit": 0,
            "activeDeadlineSeconds": 330,
            "template": {
                "metadata": {"labels": {"goblin-king.io/trust": "network-control"}},
                "spec": {
                    "serviceAccountName": payload["environment"]["release"] + "-network",
                    "automountServiceAccountToken": True,
                    "restartPolicy": "Never",
                    "securityContext": {
                        "runAsNonRoot": True,
                        "runAsUser": 65532,
                        "runAsGroup": 65532,
                        "fsGroup": 65532,
                        "seccompProfile": {"type": "RuntimeDefault"},
                    },
                    "containers": [
                        {
                            "name": "worker",
                            "image": image,
                            "env": [
                                {"name": "GOBLIN_INPUT_PATH", "value": "/input/input.json"},
                                {"name": "GOBLIN_RESULT_PATH", "value": "/tmp/result.json"},
                                {
                                    "name": "GOBLIN_CONTROL_NAMESPACE",
                                    "value": installed["control_namespace"],
                                },
                            ],
                            "securityContext": {
                                "allowPrivilegeEscalation": False,
                                "readOnlyRootFilesystem": True,
                                "capabilities": {"drop": ["ALL"]},
                            },
                            "resources": {
                                "requests": {"cpu": "100m", "memory": "128Mi"},
                                "limits": {"cpu": "1", "memory": "512Mi"},
                            },
                            "volumeMounts": [
                                {"name": "input", "mountPath": "/input", "readOnly": True},
                                {"name": "tmp", "mountPath": "/tmp"},
                            ],
                        }
                    ],
                    "volumes": [
                        {"name": "input", "configMap": {"name": config_name}},
                        {"name": "tmp", "emptyDir": {"sizeLimit": "64Mi"}},
                    ],
                },
            },
        },
    }
    result = inspected_result(ledger.job(stage, manifest, timeout=330))
    if result.status != "success" or not result.data.get("networking"):
        raise RecoveryHold("networking phase did not verify its effects: " + str(result.error))
    return result.data["networking"]
