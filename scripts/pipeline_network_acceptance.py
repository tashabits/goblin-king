"""Required networking observations layered onto the complete delivery acceptance suite."""

from __future__ import annotations

import json
import time
from pathlib import Path

from pipeline_network_fixture import ExternalFixture, external_ingress
from pipeline_networking_prepare import prepare

SCENARIOS = (
    "network-installation-matrix",
    "network-upgrade",
    "network-existing-gateway",
    "network-admin-api-cli",
    "network-authorization",
    "network-four-releases",
    "network-external-ingress",
    "network-negative-ingress",
    "network-tls-lifecycle",
    "network-allowed-egress",
    "network-denied-egress",
    "network-dns-policy",
    "network-revocation",
    "network-fail-closed",
    "network-activation",
    "network-rollout-recovery",
    "network-concurrency-restart",
    "network-recording-recovery",
    "network-node-loss",
    "network-backup-restore",
    "network-cleanup",
)
ROOT = Path(__file__).resolve().parents[1]


class NetworkAcceptance:
    def __init__(self, delivery):
        self.delivery, self.proof = delivery, delivery.proof
        self.output = self.proof.output / "networking"
        self.output.mkdir(parents=True, exist_ok=True)
        self.options = self.proof.state["networking"]
        self.fixture = ExternalFixture(
            self.output / "upstream",
            self.proof.state["run_id"],
            self.proof.state["images"]["controller"]["digest"],
        )

    def save(self, name, evidence):
        path = self.output / (name + ".json")
        path.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
        return {"evidence": str(path.relative_to(self.proof.output))}

    def current(self, implementation, environment):
        release = self.delivery.release(implementation, environment)
        return self.delivery.request("/pipeline/releases/" + release["key"] + "/networking")

    def probe(self, implementation, environment, name):
        release = self.delivery.release(implementation, environment)
        result = self.delivery.request(
            "/pipeline/releases/" + release["key"] + "/networking/probe",
            {"expected_version": release["version"], "idempotency_key": name},
        )
        return self.delivery.operation(result["operation_id"])

    def apply(self, implementation, environment, configuration, name):
        result = self.begin(implementation, environment, configuration, name)
        settled = self.delivery.operation(result["operation_id"])
        if settled["status"] != "completed":
            raise RuntimeError("networking operation did not complete: " + settled["id"])
        return {"request": result, "operation": settled}

    def begin(self, implementation, environment, configuration, name):
        delivery = self.delivery
        release = delivery.release(implementation, environment)
        preview = delivery.request(
            "/pipeline/networking/preview",
            {
                "release_key": release["key"],
                "configuration": configuration,
            },
        )
        result = delivery.request(
            "/pipeline/releases/" + release["key"] + "/networking",
            {
                "configuration": configuration,
                "preview_id": preview["preview_id"],
                "expected_version": preview["expected_version"],
                "idempotency_key": name,
            },
        )
        return result | {"preview": preview}

    def configuration(self, implementation, environment):
        delivery = self.delivery
        policy = json.loads((delivery.output / "networking-values.json").read_text())["policy"]
        allowed = policy["releases"][f"{delivery.prefix}-{implementation}-{environment}/app"]
        return {
            "ingress": {
                "enabled": True,
                "gateway": environment,
                "exposure": "external",
                "hostname": f"{implementation}.{environment}.{delivery.prefix}.example.test",
                "certificate": "local",
            },
            "egress": {
                "enabled": True,
                "hosts": allowed["outbound_hosts"],
                "direct": allowed["direct_destinations"],
            },
        }

    def installation(self, original):
        delivery, proof = self.delivery, self.proof
        original_evidence = original()
        upstream = self.fixture.prepare()
        prepare(
            delivery.output,
            proof.state["images"],
            context=proof.state["context"],
            project=delivery.project,
            gateway_class=self.options["gateway_class"],
            controller_name=self.options["controller_name"],
            external_fixture=upstream,
            resume=(delivery.output / "networking-values.json").exists(),
        )
        namespace = delivery.prefix + "-ingress"
        current = json.loads(proof.kube("get", "namespace", namespace, "-o", "json"))
        if (
            current["metadata"].get("labels", {}).get("goblin-king.io/acceptance")
            != proof.state["run_id"]
        ):
            raise RuntimeError("network package has no acceptance ownership")
        proof.state["namespaces"][namespace] = {
            "name": namespace,
            "uid": current["metadata"]["uid"],
        }
        proof.save()
        proof.command(
            [
                "helm",
                "--kube-context",
                proof.state["context"],
                "upgrade",
                "delivery",
                str(ROOT / "charts/goblin-king"),
                "-n",
                delivery.control,
                "-f",
                str(delivery.output / "values.json"),
                "--wait",
                "--timeout=300s",
            ],
            timeout=330,
        )
        validation = delivery.request(
            "/admin/workers/validate-kubernetes",
            {
                "kinds": [
                    "pipeline.build",
                    "pipeline.deploy.test",
                    "pipeline.deploy.prod",
                    "pipeline.network.test",
                    "pipeline.network.prod",
                ],
                "input": {"validate": True},
                "require_success": True,
                "timeout_seconds": 90,
            },
        )
        if not all(item["ok"] for item in validation["validations"]):
            raise RuntimeError("networking execution validation failed")
        return self.save(
            "installation",
            {"delivery": original_evidence, "upstream": upstream, "validation": validation},
        )

    def onboard(self, original, implementation, repository_id):
        result = original(implementation, repository_id)
        for environment in ("test", "prod"):
            result["networking_" + environment] = self.apply(
                implementation,
                environment,
                self.configuration(implementation, environment),
                "initial-network-" + implementation + "-" + environment,
            )
            if self.delivery.release(implementation, environment)["document"]["observed"]:
                raise RuntimeError("network attachment deployed an existing head")
        return result

    def four_releases(self):
        evidence = {}
        for implementation in ("python", "node"):
            for environment in ("test", "prod"):
                release = self.delivery.release(implementation, environment)
                network = self.delivery.request(
                    "/pipeline/releases/" + release["key"] + "/networking"
                )
                if not release["document"]["observed"] or not network["observed"]:
                    raise RuntimeError("configured release has no verified observation")
                if release["document"]["observed"].get("networking") != network["observed"]:
                    raise RuntimeError("release and networking observations differ")
                evidence[implementation + "-" + environment] = {
                    "release": release,
                    "networking": network,
                }
        return self.save("four-releases", evidence)

    def external(self):
        evidence = {
            implementation + "-" + environment: external_ingress(
                self.proof, self.delivery, implementation, environment
            )
            for implementation in ("python", "node")
            for environment in ("test", "prod")
        }
        return self.save("external-ingress", evidence)

    def egress(self):
        from pipeline_network_probes import EGRESS, NODE_TLS

        delivery, proof = self.delivery, self.proof
        upstream = json.loads(self.fixture.path.read_text())
        node = next(
            item
            for item in proof.state["nodes"]["items"]
            if "node-role.kubernetes.io/control-plane" in item["metadata"]["labels"]
        )
        address = next(
            item["address"] for item in node["status"]["addresses"] if item["type"] == "InternalIP"
        )
        arguments = upstream | {
            "ca": (self.fixture.output / "tls.crt").read_text(),
            "control": delivery.control,
            "node_address": address,
        }
        observations = {}
        for environment in ("test", "prod"):
            other = "prod" if environment == "test" else "test"
            arguments.update(
                other_proxy=delivery.prefix + "-node-" + environment,
                other_namespace=delivery.prefix + "-python-" + other,
            )
            for namespace, deployment in (
                (arguments["other_proxy"], "app-egress"),
                (arguments["other_namespace"], "app"),
                (delivery.control, "delivery-redis"),
            ):
                proof.kube(
                    "-n",
                    namespace,
                    "rollout",
                    "status",
                    "deployment/" + deployment,
                    "--timeout=60s",
                )
            observations["python-" + environment] = json.loads(
                proof.kube(
                    "-n",
                    delivery.prefix + "-python-" + environment,
                    "exec",
                    "-i",
                    "deployment/app",
                    "--",
                    "python",
                    "-c",
                    EGRESS,
                    document=arguments,
                )
            )
            observations["node-tls-" + environment] = json.loads(
                proof.kube(
                    "-n",
                    delivery.prefix + "-node-" + environment,
                    "exec",
                    "-i",
                    "deployment/app",
                    "--",
                    "node",
                    "--input-type=module",
                    "-e",
                    NODE_TLS,
                    "probe",
                    document=arguments,
                )
            )
        return self.save(
            "controlled-egress", {"observations": observations, "upstream": self.fixture.control()}
        )

    def denied_egress(self):
        path = self.output / "controlled-egress.json"
        evidence = json.loads(path.read_text())
        for environment in ("test", "prod"):
            observed = evidence["observations"]["python-" + environment]
            if any(
                observed[name] != "denied"
                for name in (
                    "direct-bypass",
                    "node-api",
                    "control-redis",
                    "other-proxy",
                    "other-environment",
                )
            ):
                raise RuntimeError("required identity boundary was not denied")
        return {"evidence": str(path.relative_to(self.proof.output))}

    def dns(self):
        from pipeline_network_probes import DNS

        upstream = json.loads(self.fixture.path.read_text())
        node = next(
            item
            for item in self.proof.state["nodes"]["items"]
            if "node-role.kubernetes.io/control-plane" in item["metadata"]["labels"]
        )
        address = next(
            item["address"] for item in node["status"]["addresses"] if item["type"] == "InternalIP"
        )

        def probe():
            return json.loads(
                self.proof.kube(
                    "-n",
                    self.delivery.prefix + "-python-test",
                    "exec",
                    "deployment/app",
                    "--",
                    "python",
                    "-c",
                    DNS,
                )
            )

        before = probe()
        if not all(value == 200 for value in before.values()):
            raise RuntimeError("DNS denial requires successful controlled baseline requests")
        try:
            self.fixture.control(
                {
                    "rebind.fixture.example.test": [address],
                    "mixed.fixture.example.test": [upstream["address"], address, "::1"],
                }
            )
            deadline = time.monotonic() + 90
            while True:
                after = probe()
                if after == {"allowed": 200, "rebind": 403, "mixed": 403}:
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError("DNS boundary failed: " + str(after))
                time.sleep(2)
            result = self.save(
                "dns-boundaries",
                {"before": before, "after": after, "upstream": self.fixture.control()},
            )
        finally:
            self.fixture.control(
                {
                    "rebind.fixture.example.test": [upstream["address"]],
                    "mixed.fixture.example.test": [upstream["address"]],
                }
            )
            deadline = time.monotonic() + 90
            while probe() != before:
                if time.monotonic() >= deadline:
                    raise RuntimeError("DNS fixture did not recover after restoration")
                time.sleep(2)
        from pipeline_network_lifecycle_acceptance import NetworkLifecycleAcceptance

        self.save("additive-policy", NetworkLifecycleAcceptance(self).additive_policy())
        return result

    def implementations(self, original):
        from pipeline_network_authorization_acceptance import authorization
        from pipeline_network_fault_acceptance import NetworkFaultAcceptance
        from pipeline_network_ingress_acceptance import IngressAcceptance
        from pipeline_network_lifecycle_acceptance import NetworkLifecycleAcceptance
        from pipeline_network_ui_acceptance import browser

        installation, onboard = self.delivery.installation, self.delivery.onboard
        self.delivery.onboard = lambda implementation, repository_id: self.onboard(
            onboard, implementation, repository_id
        )
        return original | {
            "fresh-installation": lambda: self.installation(installation),
            "network-four-releases": self.four_releases,
            "network-external-ingress": self.external,
            "network-allowed-egress": self.egress,
            "network-denied-egress": self.denied_egress,
            "network-dns-policy": self.dns,
            "network-negative-ingress": IngressAcceptance(self).negative,
            "network-tls-lifecycle": IngressAcceptance(self).tls,
            "network-recording-recovery": NetworkLifecycleAcceptance(self).recording,
            "network-activation": NetworkLifecycleAcceptance(self).activation,
            "network-rollout-recovery": NetworkLifecycleAcceptance(self).rollout,
            "network-authorization": lambda: authorization(self),
            "network-concurrency-restart": NetworkFaultAcceptance(self).run,
            "network-admin-api-cli": lambda: browser(self),
        }
