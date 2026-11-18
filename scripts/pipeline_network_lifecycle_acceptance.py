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
            "network-rollout-recovery",
            {
                "application": prior,
                "current_health": health,
                "route_failure": self.route_failure(),
                "first_exposure": self.first_exposure(),
            },
        )

    def route_failure(self):
        from pipeline_network_revocation_acceptance import DENIED

        network, delivery, proof = self.network, self.delivery, self.proof
        original = copy.deepcopy(network.current("python", "test")["desired"])
        before = delivery.release("python", "test")["document"]["observed"]
        changed = copy.deepcopy(original)
        changed["ingress"].update(path="/slow", path_type="Exact", timeout_seconds=1)
        changed["egress"].update(hosts=[], direct=[])
        identity = "route-failure-" + uuid4().hex
        started = network.begin("python", "test", changed, identity)
        failed = delivery.operation(started["operation_id"])
        if failed["status"] != "failed" or not failed["document"]["result"]["data"].get(
            "route_rollback"
        ):
            raise RuntimeError("unhealthy route did not report failure with a verified fallback")
        current = network.current("python", "test")
        expected = changed | {"ingress": original["ingress"]}
        if current["observed"]["configuration"] != expected or current["desired"] != changed:
            raise RuntimeError("route fallback lost the requested state or widened outbound access")
        after = delivery.release("python", "test")["document"]["observed"]
        if (before["image"], before["helm_revision"]) != (after["image"], after["helm_revision"]):
            raise RuntimeError("route fallback rebuilt or redeployed the application")
        # Verify the restored route while desired state retains the failed change.
        health = external_ingress(proof, delivery, "python", "test")
        denied = json.loads(
            proof.kube(
                "-n",
                delivery.prefix + "-python-test",
                "exec",
                "-i",
                "deployment/app",
                "--",
                "python",
                "-c",
                DENIED,
                document=json.loads(network.fixture.path.read_text()),
            )
        )
        restored = network.apply("python", "test", original, identity + "-restore")
        return {
            "failed": failed,
            "networking": current,
            "health": health,
            "denied": denied,
            "restored": restored,
        }

    def first_exposure(self):
        import base64
        import subprocess

        from pipeline_networking_prepare import certificate

        network, delivery, proof = self.network, self.delivery, self.proof
        original = copy.deepcopy(network.current("python", "test")["desired"])
        off = copy.deepcopy(original)
        off["ingress"]["enabled"] = False
        identity = "first-exposure-" + uuid4().hex
        network.apply("python", "test", off, identity + "-off")
        values = json.loads((delivery.output / "networking-values.json").read_text())
        binding = values["policy"]["gateways"][original["ingress"]["gateway"]]
        namespace, name = (
            binding["namespace"],
            binding["certificates"][original["ingress"]["certificate"]],
        )

        def secret():
            return json.loads(
                subprocess.check_output(
                    [
                        "kubectl",
                        "--context",
                        proof.state["context"],
                        "-n",
                        namespace,
                        "get",
                        "secret",
                        name,
                        "-o",
                        "json",
                    ],
                    timeout=30,
                )
            )

        prior = secret()
        cert, key = certificate(["unapproved.example.test"])
        modified = copy.deepcopy(prior)
        modified["data"] = {
            "tls.crt": base64.b64encode(cert).decode(),
            "tls.key": base64.b64encode(key).decode(),
        }
        try:
            proof.kube("replace", "-f", "-", document=modified)
            started = network.begin("python", "test", original, identity)
            held = delivery.operation(started["operation_id"])
            current = network.current("python", "test")
            if (
                held["status"] != "held"
                or current["observed"]["configuration"]["ingress"]["enabled"]
            ):
                raise RuntimeError("failed first exposure invented a healthy previous route")
            if held["document"].get("result", {}).get("data", {}).get("route_rollback"):
                raise RuntimeError("first exposure claimed a nonexistent route rollback")
        finally:
            actual = secret()
            if actual["metadata"]["uid"] != prior["metadata"]["uid"]:
                raise RuntimeError("first exposure certificate identity changed")
            actual["data"] = prior["data"]
            proof.kube("replace", "-f", "-", document=actual)
        preview = delivery.request(
            "/pipeline/networking/preview",
            {"release_key": held["release_key"], "configuration": original},
        )
        recovery = delivery.request(
            "/pipeline/operations/" + held["id"] + "/recover",
            {
                "expected_version": held["version"],
                "idempotency_key": identity + "-recover",
                "networking_preview_id": preview["preview_id"],
            },
        )
        settled = delivery.operation(recovery["operation_id"])
        if settled["status"] != "completed":
            raise RuntimeError("first exposure did not recover after certificate repair")
        return {
            "held": held,
            "prior_observation": current["observed"],
            "recovered": settled,
            "external": external_ingress(proof, delivery, "python", "test"),
        }
