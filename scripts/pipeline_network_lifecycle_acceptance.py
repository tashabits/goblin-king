"""Observe network state through release changes, recording repair and policy failures."""

from __future__ import annotations

import copy
import json
from uuid import uuid4

from pipeline_fault_acceptance import FaultAcceptance
from pipeline_network_fixture import external_ingress
from pipeline_network_ingress_acceptance import IngressAcceptance


class NetworkLifecycleAcceptance:
    def __init__(self, network):
        self.network = network
        self.delivery, self.proof = network.delivery, network.proof

    def resources(self, implementation="python", environment="test"):
        items = json.loads(
            self.proof.kube(
                "-n",
                f"{self.delivery.prefix}-{implementation}-{environment}",
                "get",
                "httproutes,backendtrafficpolicies,networkpolicies,deployments,services,configmaps",
                "-l",
                "goblin-king.io/networking=true",
                "-o",
                "json",
            )
        )["items"]
        return {
            item["kind"] + "/" + item["metadata"]["name"]: {
                "uid": item["metadata"]["uid"],
                "generation": item["metadata"].get("generation"),
                "spec": item.get("spec"),
                "data": item.get("data"),
            }
            for item in items
        }

    def recording(self):
        network, delivery, proof = self.network, self.delivery, self.proof
        configuration = copy.deepcopy(network.current("python", "test")["desired"])
        configuration["ingress"]["timeout_seconds"] = (
            29 if configuration["ingress"]["timeout_seconds"] == 30 else 30
        )
        identity = "network-recording-" + str(uuid4())
        delivery.request("/_fixture/recording", {"available": False}, provider=True)
        try:
            operation = network.begin("python", "test", configuration, identity)
            waiting = delivery.wait(
                "network observation awaiting Git recording",
                lambda: next(
                    (
                        row
                        for row in delivery.collection("operations")
                        if row["id"] == operation["operation_id"]
                        and row["status"] == "recording"
                        and row["document"].get("record_error")
                    ),
                    None,
                ),
                timeout=600,
            )
            before = network.current("python", "test")
            resources = self.resources()
            jobs = json.loads(proof.kube("-n", delivery.control, "get", "jobs", "-o", "json"))
            concurrent = FaultAcceptance(delivery).concurrent_record({"id": identity})
            health = external_ingress(proof, delivery, "python", "test")
        finally:
            delivery.request("/_fixture/recording", {"available": True}, provider=True)
        completed = delivery.operation(operation["operation_id"])
        after = network.current("python", "test")
        after_jobs = json.loads(proof.kube("-n", delivery.control, "get", "jobs", "-o", "json"))
        if (
            completed["status"] != "completed"
            or before["observed"] != after["observed"]
            or self.resources() != resources
            or {j["metadata"]["uid"] for j in jobs["items"]}
            != {j["metadata"]["uid"] for j in after_jobs["items"]}
        ):
            raise RuntimeError("recording repair repeated or changed network execution")
        preserved = proof.kube(
            "-n",
            delivery.provider,
            "exec",
            "deploy/provider",
            "--",
            "git",
            "-C",
            "/data/release-state",
            "show",
            "main:" + concurrent["path"],
        )
        if preserved != concurrent["content"]:
            raise RuntimeError("network recording discarded a concurrent service record")
        return network.save(
            "network-recording-recovery",
            {
                "waiting": waiting,
                "completed": completed,
                "before": before,
                "after": after,
                "resources": resources,
                "concurrent": concurrent,
                "health": health,
                "job_uids": [j["metadata"]["uid"] for j in jobs["items"]],
            },
        )

    def activation(self):
        network, delivery, proof = self.network, self.delivery, self.proof
        identity = "network-activation-" + str(uuid4())
        before = delivery.release("python", "test")
        off = delivery.operation(
            delivery.action(identity + "-off", "python", "test", "deactivate")["operation_id"]
        )
        if off["status"] != "completed":
            raise RuntimeError("network deactivation did not complete")
        observed = network.current("python", "test")
        if observed["observed"]["active"]:
            raise RuntimeError("deactivation left an active network observation")
        resources = self.resources()
        if any(name.startswith(("HTTPRoute/", "Deployment/", "Service/")) for name in resources):
            raise RuntimeError("inactive route or outbound proxy remains")
        external = IngressAcceptance(network).client("withdrawn")
        namespace = delivery.prefix + "-python-test"
        workloads = json.loads(
            proof.kube(
                "-n",
                namespace,
                "get",
                "deployments,cronjobs,pods",
                "-l",
                "app.kubernetes.io/instance=app",
                "-o",
                "json",
            )
        )["items"]
        for item in workloads:
            if (
                (item["kind"] == "Deployment" and item["spec"]["replicas"] != 0)
                or (item["kind"] == "CronJob" and not item["spec"]["suspend"])
                or item["kind"] == "Pod"
            ):
                raise RuntimeError("inactive application activity remains")
        on = delivery.operation(
            delivery.action(identity + "-on", "python", "test", "activate")["operation_id"]
        )
        if on["status"] != "completed":
            raise RuntimeError("network activation did not complete")
        health = external_ingress(proof, delivery, "python", "test")
        previous = json.loads(before["document"]["observed"]["health"]["body"])
        after = json.loads(health["client"]["observations"]["https"]["body"])
        if previous["sentinel"] != after["sentinel"]:
            raise RuntimeError("network off/on lost persistent data")
        return network.save(
            "network-activation",
            {
                "off": off,
                "on": on,
                "inactive": observed,
                "withdrawn": external,
                "workloads": workloads,
                "resources": resources,
                "health": health,
            },
        )

    def additive_policy(self):
        network, delivery, proof = self.network, self.delivery, self.proof
        namespace = delivery.prefix + "-python-test"
        name = "acceptance-additive-" + uuid4().hex[:8]
        before = self.resources()
        proof.apply(
            {
                "apiVersion": "networking.k8s.io/v1",
                "kind": "NetworkPolicy",
                "metadata": {"namespace": namespace, "name": name},
                "spec": {
                    "podSelector": {"matchLabels": {"goblin-king.io/nonexistent": name}},
                    "policyTypes": ["Egress"],
                    "egress": [{}],
                },
            }
        )
        try:
            result = network.probe("python", "test", "additive-" + name)
            if result["status"] != "failed" or "additional outbound policy" not in str(result):
                raise RuntimeError("unexpected additive policy was not diagnosed")
            if before != self.resources():
                raise RuntimeError("diagnostic probe mutated installed network resources")
        finally:
            proof.kube("-n", namespace, "delete", "networkpolicy", name)
        healthy = network.probe("python", "test", "additive-restored-" + name)
        if healthy["status"] != "completed":
            raise RuntimeError("network health did not recover after policy removal")
        return {"failed_probe": result, "restored_probe": healthy}

    def rollout(self):
        # The delivery failure scenario runs with networking attached. Retain its exact evidence.
        path = self.delivery.output / "failed-rollout.json"
        prior = json.loads(path.read_text())
        failed = prior["operation"]
        restored = prior["restored"]["document"]["observed"]
        if failed["status"] != "failed" or not restored.get("networking", {}).get("route_health"):
            raise RuntimeError("failed application rollout has no verified network restoration")
        health = external_ingress(self.proof, self.delivery, "node", "test")
        return self.network.save(
            "network-rollout-recovery", {"application": prior, "current_health": health}
        )
