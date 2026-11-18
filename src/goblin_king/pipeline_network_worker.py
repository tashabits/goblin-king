"""Apply one authenticated networking operation with a dedicated Kubernetes identity."""

import json
import os
from pathlib import Path
from urllib.request import Request, urlopen

from kubernetes import client

from goblin_king.contracts import GoblinResult
from goblin_king.pipeline_faults import configured_gate
from goblin_king.pipeline_kubernetes import RecoveryHold, RetainedExecution
from goblin_king.pipeline_network_runtime import NetworkRuntime
from goblin_king.pipeline_networking import NetworkingPolicy
from goblin_king.runtime_helpers import current_kubernetes_namespace, kubernetes_clients


def configure(payload):
    batch, core = kubernetes_clients()
    namespace = current_kubernetes_namespace()
    installed = json.loads(
        core.read_namespaced_config_map("pipeline-worker-config", namespace).data["config.json"]
    )
    policy = NetworkingPolicy.model_validate(installed.get("networking", {}))
    if payload["policy_revision"] != policy.revision:
        raise RecoveryHold("networking policy changed after admission")
    environment = payload["environment"]
    policy.validate_configuration(
        payload["configuration"],
        identity=environment["namespace"] + "/" + environment["release"],
        project_id=payload["project_id"],
        environment=environment["name"],
    )
    token = Path("/var/run/secrets/kubernetes.io/serviceaccount/token").read_text()
    request = Request(
        installed["api_url"]
        + "/pipeline/internal/operations/"
        + payload["operation_id"]
        + "/claim",
        data=b"{}",
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
    )
    with urlopen(request, timeout=15) as response:
        claim = json.load(response)
    if claim != payload:
        raise RecoveryHold("networking claim differs from durable execution")
    identity = {
        key: value for key, value in payload.items() if key not in {"recover", "recovery_attempt"}
    }
    ledger = RetainedExecution(
        core,
        batch,
        environment["namespace"],
        payload["operation_id"],
        identity,
        fault_hook=configured_gate(core, namespace, installed),
    )
    if ledger.document["result"]:
        return GoblinResult.model_validate(ledger.document["result"])
    runtime = NetworkRuntime(
        client.ApiClient(),
        ledger,
        policy,
        namespace=environment["namespace"],
        release=environment["release"],
        project_id=payload["project_id"],
        environment=environment["name"],
    )
    observed = runtime.configure(
        payload["configuration"],
        operation_id=payload["operation_id"],
        active=payload["active"],
        port=payload["port"],
        previous=payload.get("previous"),
        health_path=payload["health_path"],
    )
    result = GoblinResult.ok(data={"operation_id": payload["operation_id"], "networking": observed})
    ledger.finish(result.model_dump(mode="json"))
    return result


def main():
    payload = json.loads(Path(os.environ["GOBLIN_INPUT_PATH"]).read_text())
    try:
        result = (
            GoblinResult.ok(data={"validated": True})
            if payload == {"validate": True}
            else configure(payload)
        )
    except Exception as error:
        result = GoblinResult.failed(
            error=str(error)
            if isinstance(error, (RecoveryHold, ValueError))
            else type(error).__name__,
            data={"recovery_hold": True},
        )
    raw = result.model_dump_json()
    Path(os.environ["GOBLIN_RESULT_PATH"]).write_text(raw)
    print(raw, flush=True)


if __name__ == "__main__":
    main()
