"""Execute one exclusively claimed release using the installed HTTP service profile."""

import json
import os
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import NAMESPACE_URL, uuid5

from kubernetes import client

from goblin_king.contracts import GoblinResult
from goblin_king.pipeline_deployment import HelmRelease
from goblin_king.pipeline_kubernetes import RecoveryHold, RetainedExecution
from goblin_king.runtime_helpers import current_kubernetes_namespace, kubernetes_clients


def deploy(payload):
    batch, core = kubernetes_clients()
    config = json.loads(
        core.read_namespaced_config_map(
            "pipeline-worker-config", current_kubernetes_namespace()
        ).data["config.json"]
    )
    environment = payload["environment"]
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
        raise RecoveryHold("release claim was rejected: HTTP " + str(error.code)) from None
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
        core, client.AppsV1Api(), batch, environment["namespace"], environment["release"]
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
