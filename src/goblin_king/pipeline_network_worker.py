"""Apply one authenticated networking operation with a dedicated Kubernetes identity."""

import json
import os
from pathlib import Path
from urllib.request import Request, urlopen
from uuid import NAMESPACE_URL, uuid5

from kubernetes import client

from goblin_king.contracts import GoblinResult
from goblin_king.pipeline_faults import configured_gate
from goblin_king.pipeline_kubernetes import RecoveryHold, RetainedExecution
from goblin_king.pipeline_network_runtime import NetworkRuntime
from goblin_king.pipeline_networking import NetworkingPolicy
from goblin_king.runtime_helpers import current_kubernetes_namespace, kubernetes_clients


def authorized_route_fallback(payload, policy):
    previous = payload.get("previous")
    if (
        payload.get("parent_stage")
        or payload.get("probe_only")
        or not payload["active"]
        or not previous
        or not previous.get("active")
        or not payload["configuration"]["ingress"]["enabled"]
        or not previous["configuration"]["ingress"]["enabled"]
    ):
        return None
    configuration = payload["configuration"] | {"ingress": previous["configuration"]["ingress"]}
    target = payload["environment"]
    policy.validate_configuration(
        configuration,
        identity=target["namespace"] + "/" + target["release"],
        project_id=payload["project_id"],
        environment=target["name"],
    )
    return configuration


def configure(payload):
    batch, core = kubernetes_clients()
    namespace = os.environ.get("GOBLIN_CONTROL_NAMESPACE") or current_kubernetes_namespace()
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
        + (
            "/network-claims/" + payload["parent_stage"]
            if payload.get("parent_stage")
            else "/claim"
        ),
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
        str(uuid5(NAMESPACE_URL, payload["parent_ledger"] + "/" + payload["parent_stage"]))
        if payload.get("parent_stage")
        else (
            str(
                uuid5(
                    NAMESPACE_URL, payload["operation_id"] + "/policy/" + payload["policy_revision"]
                )
            )
            if payload.get("reapproved_policy_from")
            else payload["operation_id"]
        ),
        identity,
        fault_hook=configured_gate(core, namespace, installed),
    )
    if ledger.document["result"]:
        return GoblinResult.model_validate(ledger.document["result"])

    def execute(effect_ledger, configuration, previous):
        runtime = NetworkRuntime(
            client.ApiClient(),
            effect_ledger,
            policy,
            namespace=environment["namespace"],
            release=environment["release"],
            project_id=payload["project_id"],
            environment=environment["name"],
        )
        return runtime.configure(
            configuration,
            operation_id=payload["operation_id"],
            active=payload["active"],
            port=payload["port"],
            previous=previous,
            health_path=payload["health_path"],
            prepare_only=payload.get("prepare_only", False),
            probe_only=payload.get("probe_only", False),
            expected_image=payload.get("expected_image"),
        )

    if not ledger.document.get("route_fallback"):
        try:
            observed = execute(ledger, payload["configuration"], payload.get("previous"))
        except (RecoveryHold, ValueError) as error:
            fallback = authorized_route_fallback(payload, policy)
            if fallback is None:
                raise
            ledger.document["route_fallback"] = {"configuration": fallback, "error": str(error)}
            ledger.save()
    fallback = ledger.document.get("route_fallback")
    if fallback:
        if authorized_route_fallback(payload, policy) != fallback["configuration"]:
            raise RecoveryHold("retained route fallback is no longer authorized")
        fallback_ledger = RetainedExecution(
            core,
            batch,
            environment["namespace"],
            str(uuid5(NAMESPACE_URL, ledger.name + "/authorized-route-fallback")),
            identity | {"fallback": fallback["configuration"]},
            fault_hook=configured_gate(core, namespace, installed),
        )
        observed = execute(
            fallback_ledger,
            fallback["configuration"],
            {
                "configuration": payload["configuration"],
                "policy_revision": payload["policy_revision"],
            },
        )
    result = GoblinResult.ok(
        data={
            "operation_id": payload["operation_id"],
            "networking": observed,
            **({"route_rollback": True, "route_error": fallback["error"]} if fallback else {}),
        }
    )
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
