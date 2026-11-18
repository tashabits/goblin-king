"""Execute one exclusively claimed release using the installed HTTP service profile."""

import json
import os
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import NAMESPACE_URL, uuid5

from kubernetes import client
from kubernetes.client.exceptions import ApiException

from goblin_king.contracts import GoblinResult
from goblin_king.pipeline_deployment import HelmRelease
from goblin_king.pipeline_faults import configured_gate
from goblin_king.pipeline_kubernetes import RecoveryHold, RetainedExecution
from goblin_king.pipeline_network_child import phase as network_phase
from goblin_king.runtime_helpers import current_kubernetes_namespace, kubernetes_clients


def recorded_first_failure(core, payload):
    """Recover an acknowledged first-install failure without repeating its Helm operation."""
    if not payload.get("recover") or payload.get("previous"):
        return None
    try:
        previous = json.loads(
            core.read_namespaced_config_map(
                "pipeline-" + payload["operation_id"], payload["environment"]["namespace"]
            ).data["execution.json"]
        )
    except ApiException as error:
        if error.status == 404:
            return None
        raise
    original = previous["identity"]["request"]
    if any(
        original.get(key) != payload.get(key)
        for key in ("operation_id", "release_key", "build_id", "environment", "values")
    ):
        raise RecoveryHold("prior deployment result differs from selected recovery intent")
    result = previous.get("result") or {}
    observed = result.get("data", {}).get("release") or {}
    return (
        observed
        if result.get("status") == "success"
        and observed.get("status") == "failed"
        and not observed.get("rollback")
        else None
    )


def deploy(payload):
    batch, core = kubernetes_clients()
    config = json.loads(
        core.read_namespaced_config_map(
            "pipeline-worker-config", current_kubernetes_namespace()
        ).data["config.json"]
    )
    environment = payload["environment"]
    fault_hook = configured_gate(core, current_kubernetes_namespace(), config, "deployment")
    target = config["releases"].get(environment["namespace"] + "/" + environment["release"])
    if target is None or target["environment"] != environment["name"]:
        raise ValueError("release is outside the installed deployment profile")
    token = Path("/var/run/secrets/kubernetes.io/serviceaccount/token").read_text()
    request = Request(
        config["api_url"] + "/pipeline/internal/operations/" + payload["operation_id"] + "/claim",
        data=b"{}",
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=15) as response:
            claim = json.load(response)
    except HTTPError as error:
        detail = ""
        try:
            body = json.loads(error.read(4096))
            if isinstance(body, dict) and isinstance(body.get("detail"), str):
                detail = ": " + body["detail"][:240]
        except (ValueError, OSError):
            pass
        raise RecoveryHold("release claim was rejected: HTTP " + str(error.code) + detail) from None
    if claim.get("superseded"):
        return GoblinResult.ok(data={"superseded": True})
    if claim != payload:
        raise RecoveryHold("release claim differs from dispatched input")
    ledger = RetainedExecution(
        core,
        batch,
        environment["namespace"],
        (
            str(
                uuid5(
                    NAMESPACE_URL,
                    payload["operation_id"] + "/recovery/" + str(payload["recovery_attempt"]),
                )
            )
            if payload.get("recover")
            else payload["operation_id"]
        ),
        {"request": payload, "configuration_revision": config["revision"]},
        fault_hook=fault_hook,
    )
    if ledger.document["result"]:
        return GoblinResult.model_validate(ledger.document["result"])
    if ledger.document.get("helm_started"):
        raise RecoveryHold("Helm effect has no acknowledged result; inspect actual release state")
    values = payload["values"] | {
        "pullSecret": target["pull_secret"],
        "persistence": target["persistence"],
    }
    release = HelmRelease(
        core,
        client.AppsV1Api(),
        batch,
        environment["namespace"],
        environment["release"],
        fault_hook=fault_hook,
    )
    network = payload.get("networking")
    failed = recorded_first_failure(core, payload)
    if failed:
        actual = json.loads(
            release.helm("get", "values", environment["release"], "--output", "json")
        )
        if any(actual.get(key) != values[key] for key in ("image", "operationId")):
            raise RecoveryHold("failed first installation changed before recovery")
        release.verify(
            image=values["image"],
            active=False,
            port=values["port"],
            health_path=values["healthPath"],
        )
        if network:
            network_phase(
                ledger,
                core,
                payload=payload,
                installed=config,
                stage="network-after",
                active=False,
                previous=network.get("previous"),
                probe_only=True,
            )
        result = GoblinResult.ok(data={"operation_id": payload["operation_id"], "release": failed})
        ledger.finish(result.model_dump(mode="json"))
        return result
    if network:
        network_phase(
            ledger,
            core,
            payload=payload,
            installed=config,
            stage="network-before",
            active=values["active"],
            previous=network.get("previous"),
            prepare_only=True,
        )
    ledger.document["helm_started"] = True
    ledger.save()
    observed = (
        release.recover(values, previous=payload["previous"])
        if payload.get("recover")
        else release.apply(
            values, chart=Path("/opt/pipeline-http-service"), previous=payload["previous"]
        )
    )
    if network:
        restored = observed if observed["status"] == "success" else observed.get("rollback")
        try:
            evidence = network_phase(
                ledger,
                core,
                payload=payload,
                installed=config,
                stage="network-after",
                active=bool(restored and restored["active"]),
                previous=network.get("previous"),
                expected_image=restored["image"] if restored else None,
            )
        except RecoveryHold:
            if observed["status"] != "success" or not payload["previous"]:
                raise
            observed = release.rollback(
                payload["previous"], reason="network route verification failed after rollout"
            )
            restored = observed["rollback"]
            evidence = network_phase(
                ledger,
                core,
                payload=payload,
                installed=config,
                stage="network-rollback",
                active=restored["active"],
                previous=network.get("previous"),
                expected_image=restored["image"],
            )
        if restored:
            restored["networking"] = evidence
    result = GoblinResult.ok(data={"operation_id": payload["operation_id"], "release": observed})
    ledger.finish(result.model_dump(mode="json"))
    return result


def main():
    payload = json.loads(Path(os.environ["GOBLIN_INPUT_PATH"]).read_text())
    try:
        result = (
            GoblinResult.ok(data={"validated": True})
            if payload == {"validate": True}
            else deploy(payload)
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
