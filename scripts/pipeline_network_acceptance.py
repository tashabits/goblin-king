"""Required networking observations layered onto the complete delivery acceptance suite."""

from __future__ import annotations

import json
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

    def apply(self, implementation, environment, configuration, name):
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
        settled = delivery.operation(result["operation_id"])
        if settled["status"] != "completed":
            raise RuntimeError("networking operation did not complete: " + settled["id"])
        return {"preview": preview, "operation": settled}

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

    def implementations(self, original):
        installation, onboard = self.delivery.installation, self.delivery.onboard
        self.delivery.onboard = lambda implementation, repository_id: self.onboard(
            onboard, implementation, repository_id
        )
        return original | {
            "fresh-installation": lambda: self.installation(installation),
            "network-four-releases": self.four_releases,
            "network-external-ingress": self.external,
        }
