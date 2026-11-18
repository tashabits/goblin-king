"""Resume networking after confirmed termination at each resource-effect boundary."""

from __future__ import annotations

import copy
import json
from uuid import uuid4

from pipeline_fault_acceptance import FaultAcceptance
from pipeline_network_lifecycle_acceptance import NetworkLifecycleAcceptance

APPLY_TARGETS = (
    "NetworkPolicy/app-networking",
    "NetworkPolicy/app-egress",
    "ConfigMap/app-egress",
    "Service/app-egress",
    "Deployment/app-egress",
    "HTTPRoute/app-ingress",
    "HTTPRoute/app-redirect",
    "BackendTrafficPolicy/app-ingress-limits",
)
REMOVE_TARGETS = (
    "Deployment/app-egress",
    "Service/app-egress",
    "ConfigMap/app-egress",
    "HTTPRoute/app-ingress",
    "HTTPRoute/app-redirect",
    "BackendTrafficPolicy/app-ingress-limits",
)
CASES = (
    tuple(
        ("networking." + point, "apply/" + target, "apply")
        for target in APPLY_TARGETS
        for point in ("before_apply", "after_apply", "after_apply_ack")
    )
    + tuple(
        ("networking." + point, "remove/" + target, "disable")
        for target in REMOVE_TARGETS
        for point in ("before_remove", "after_remove", "after_remove_ack")
    )
    + tuple(
        ("networking." + point, phase + "/" + target, "disable")
        for target in ("Deployment/app", "Deployment/app-background", "CronJob/app-schedule")
        for phase in ("quiesce", "restore")
        for point in (
            "before_workload_change",
            "after_workload_change",
            "after_workload_change_ack",
        )
    )
    + tuple(
        ("networking." + point, "settle/Job/", "disable")
        for point in ("before_job_removal", "after_job_removal", "after_job_removal_ack")
    )
)


class NetworkFaultAcceptance:
    def __init__(self, network):
        self.network = network
        self.delivery, self.proof = network.delivery, network.proof
        self.faults = FaultAcceptance(self.delivery)
        self.output = network.output / "faults"
        self.output.mkdir(exist_ok=True)

    def save(self, path, document):
        path.write_text(json.dumps(document, indent=2))

    def case(self, index, point, prefix, change):
        network, delivery, proof = self.network, self.delivery, self.proof
        path = self.output / (str(index).zfill(2) + ".json")
        record = (
            json.loads(path.read_text())
            if path.exists()
            else {
                "id": "network-fault-" + str(uuid4()),
                "point": point,
                "identity_prefix": prefix,
                "configuration": network.current("python", "test")["desired"],
                "release_before": delivery.release("python", "test"),
            }
        )
        if record.get("passed"):
            return record
        desired = copy.deepcopy(record["configuration"])
        if change == "disable":
            desired["ingress"]["enabled"] = desired["egress"]["enabled"] = False
        else:
            desired["ingress"]["timeout_seconds"] = (
                29 if desired["ingress"]["timeout_seconds"] == 30 else 30
            )
        if "started" not in record:
            self.faults.wait_for_acknowledgements()
            if prefix.startswith("settle/Job/"):
                namespace = delivery.prefix + "-python-test"
                schedule = json.loads(
                    proof.kube("-n", namespace, "get", "cronjob", "app-schedule", "-o", "json")
                )
                job = {
                    "apiVersion": "batch/v1",
                    "kind": "Job",
                    "metadata": {
                        "name": "fault-schedule-" + uuid4().hex[:8],
                        "namespace": namespace,
                        "labels": schedule["spec"]["jobTemplate"]["metadata"].get("labels", {}),
                        "ownerReferences": [
                            {
                                "apiVersion": "batch/v1",
                                "kind": "CronJob",
                                "name": "app-schedule",
                                "uid": schedule["metadata"]["uid"],
                                "controller": True,
                            }
                        ],
                    },
                    "spec": copy.deepcopy(schedule["spec"]["jobTemplate"]["spec"]),
                }
                job["spec"]["template"]["spec"]["containers"][0]["command"] = [
                    "python",
                    "-c",
                    "import time;time.sleep(600)",
                ]
                job["spec"]["activeDeadlineSeconds"] = 600
                job["spec"]["template"]["spec"]["containers"][0].pop("args", None)
                proof.kube("create", "-f", "-", document=job)
            plan = {key: record[key] for key in ("id", "point", "identity_prefix")}
            plan["scope"] = "networking"
            if not prefix.startswith("settle/Job/"):
                plan["identity"] = prefix
            self.faults.gate(plan)
            record["started"] = network.begin("python", "test", desired, record["id"])
            self.save(path, record)
        operation_id = record["started"]["operation_id"]
        if "hit" not in record:
            record["hit"] = delivery.wait(
                "network effect pause", lambda: self.faults.hit(record), timeout=240
            )
            self.save(path, record)
        hit = record["hit"]
        operation = delivery.request("/pipeline/operations/" + operation_id)
        if (
            hit["pod"]["metadata"]["labels"].get("goblin-king.job-id")
            != operation["document"]["job_id"]
            or hit["marker"]["point"] != point
            or not hit["marker"]["identity"].startswith(prefix)
        ):
            raise RuntimeError("fault marker does not identify this networking operation")
        if "terminated" not in record:
            ledger = json.loads(
                proof.kube(
                    "-n",
                    delivery.prefix + "-python-test",
                    "get",
                    "configmap",
                    "pipeline-" + operation_id,
                    "-o",
                    "json",
                )
            )
            record["ledger_uid"] = ledger["metadata"]["uid"]
            record["effects_at_pause"] = json.loads(ledger["data"]["execution.json"]).get(
                "network_effects", {}
            )
            record["resources_at_pause"] = NetworkLifecycleAcceptance(network).resources()
            # Competing requests cannot acquire ownership while the paused process is alive.
            release = delivery.release("python", "test")
            record["contention"] = delivery.request(
                "/pipeline/releases/" + release["key"] + "/operations",
                {
                    "action": "deactivate",
                    "expected_version": release["version"],
                    "idempotency_key": record["id"] + "-contender",
                },
                expected=(409,),
            )
            self.faults.stop(hit)
            record["terminated"] = delivery.wait(
                "network process termination", lambda: self.faults.terminated(hit), timeout=120
            )
            self.save(path, record)
        self.faults.gate({})
        settled = delivery.operation(operation_id)
        if settled["status"] == "held":
            record["held"] = settled
            self.save(path, record)
            preview = delivery.request(
                "/pipeline/networking/preview",
                {"release_key": operation["release_key"], "configuration": desired},
            )
            record["recovery"] = delivery.request(
                "/pipeline/operations/" + operation_id + "/recover",
                {
                    "expected_version": settled["version"],
                    "idempotency_key": record["id"] + "-recover",
                    "networking_preview_id": preview["preview_id"],
                },
            )
            self.save(path, record)
            settled = delivery.operation(operation_id)
        if settled["status"] != "completed":
            raise RuntimeError("network effect did not recover: " + operation_id)
        record["settled"] = settled
        current = network.current("python", "test")
        if current["observed"]["configuration"] != record["started"]["preview"]["configuration"]:
            raise RuntimeError("recovered networking differs from the approved operation")
        release = delivery.release("python", "test")
        before, after = (
            record["release_before"]["document"]["observed"],
            release["document"]["observed"],
        )
        if before["image"] != after["image"] or before["helm_revision"] != after["helm_revision"]:
            raise RuntimeError("network recovery rebuilt or redeployed the application")
        record["resources"] = NetworkLifecycleAcceptance(network).resources()
        ledger = json.loads(
            proof.kube(
                "-n",
                delivery.prefix + "-python-test",
                "get",
                "configmap",
                "pipeline-" + operation_id,
                "-o",
                "json",
            )
        )
        if ledger["metadata"]["uid"] != record["ledger_uid"]:
            raise RuntimeError("network recovery replaced its retained execution identity")
        effects = json.loads(ledger["data"]["execution.json"])["network_effects"]
        for key, value in record["effects_at_pause"].items():
            if value.get("complete") and effects.get(key) != value:
                raise RuntimeError("recovery changed an acknowledged network effect")
        if point in {"networking.after_apply", "networking.after_apply_ack"}:
            target = prefix.removeprefix("apply/")
            if record["resources_at_pause"][target] != record["resources"][target]:
                raise RuntimeError("recovery repeated an already observed network apply")
        record["restored"] = network.apply(
            "python", "test", record["configuration"], record["id"] + "-restore"
        )
        record["passed"] = True
        self.save(path, record)
        print(
            json.dumps(
                {
                    "network_fault": point,
                    "target": prefix,
                    "operation_id": operation_id,
                    "passed": True,
                }
            ),
            flush=True,
        )
        return record

    def run(self):
        records = [self.case(index, *case) for index, case in enumerate(CASES)]
        return self.network.save(
            "network-concurrency-restart",
            {
                "required": len(CASES),
                "passed": len(records),
                "evidence": [
                    str(path.relative_to(self.proof.output)) for path in self.output.glob("*.json")
                ],
            },
        )
