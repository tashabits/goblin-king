"""Check optional package installation modes, existing ownership and cleanup on the cluster."""

from __future__ import annotations

import copy
import json
from uuid import uuid4

from pipeline_network_install import NetworkInstallation


class PackageAcceptance:
    def __init__(self, network):
        self.network = network
        self.delivery, self.proof = network.delivery, network.proof

    def matrix(self):
        proof, network = self.proof, self.network
        original = json.loads((self.delivery.output / "networking-values.json").read_text())
        evidence = {}
        for name, ingress, egress in (
            ("neither", False, False),
            ("ingress", True, False),
            ("egress", False, True),
            ("both", True, True),
        ):
            control = proof.namespace("net-" + name + "-control")
            app = proof.namespace("net-" + name + "-app")
            gateway_namespace = proof.namespace("net-" + name + "-gateway")
            policy = copy.deepcopy(original["policy"])
            policy.update(
                enabled=ingress or egress,
                ingress_available=ingress,
                egress_available=egress,
                control_namespace=control,
            )
            binding = copy.deepcopy(policy["gateways"]["test"])
            binding.update(
                namespace=gateway_namespace,
                probe_address="",
                dataplane_namespace=original["policy"]["gateways"]["test"]["dataplane_namespace"],
            )
            release = copy.deepcopy(
                next(
                    value for value in policy["releases"].values() if value["environment"] == "test"
                )
            )
            release["gateways"] = ["test"] if ingress else []
            if not egress:
                release.update(
                    outbound_hosts=[],
                    direct_destinations=[],
                    private_upstreams={},
                    proxy_dns_servers=[],
                )
            policy["gateways"] = {"test": binding} if ingress else {}
            policy["releases"] = {app + "/app": release}
            values = original | {
                "policy": policy,
                "controlNamespace": control,
                "apiServiceAccount": "matrix-api",
            }
            path = network.output / ("matrix-" + name + ".json")
            path.write_text(json.dumps(values, indent=2))
            if ingress:
                import base64

                from pipeline_networking_prepare import certificate

                cert, key = certificate(["matrix.test." + self.delivery.prefix + ".example.test"])
                proof.apply(
                    {
                        "apiVersion": "v1",
                        "kind": "Secret",
                        "metadata": {
                            "name": binding["certificates"]["local"],
                            "namespace": gateway_namespace,
                        },
                        "type": "kubernetes.io/tls",
                        "data": {
                            "tls.crt": base64.b64encode(cert).decode(),
                            "tls.key": base64.b64encode(key).decode(),
                        },
                    }
                )
            proof.command(
                [
                    "helm",
                    "--kube-context",
                    proof.state["context"],
                    "install",
                    "networking",
                    "charts/pipeline-networking",
                    "-n",
                    gateway_namespace,
                    "-f",
                    str(path),
                    "--wait",
                    "--timeout=120s",
                ],
                timeout=150,
            )
            if ingress:
                proof.kube(
                    "-n",
                    gateway_namespace,
                    "wait",
                    "gateway/test",
                    "--for=condition=Programmed",
                    "--timeout=90s",
                )
            gateways = json.loads(
                proof.kube("-n", gateway_namespace, "get", "gateways", "-o", "json")
            )["items"]
            authority = json.loads(
                proof.kube("-n", control, "get", "serviceaccounts,rolebindings", "-o", "json")
            )["items"]
            network_accounts = [
                item
                for item in authority
                if item["kind"] == "ServiceAccount"
                and item["metadata"]["name"].startswith("pipeline-network-")
            ]
            if bool(gateways) != ingress or bool(network_accounts) != (ingress or egress):
                raise RuntimeError("optional install created missing or unintended authority")
            app_resources = json.loads(
                proof.kube("-n", app, "get", "deployments,services,httproutes", "-o", "json")
            )["items"]
            if app_resources:
                raise RuntimeError(
                    "package installation exposed or activated an unattached application"
                )
            evidence[name] = {
                "namespace": gateway_namespace,
                "control": control,
                "app": app,
                "gateways": gateways,
                "authority": authority,
                "unattached_application_resources": app_resources,
            }
            # Uninstall cross-namespace authority before removing the owned fixture namespaces.
            proof.command(
                [
                    "helm",
                    "--kube-context",
                    proof.state["context"],
                    "uninstall",
                    "networking",
                    "-n",
                    gateway_namespace,
                    "--wait",
                    "--timeout=90s",
                ]
            )
            remaining = json.loads(
                proof.kube("-n", control, "get", "serviceaccounts,rolebindings", "-o", "json")
            )["items"]
            if any(item["metadata"]["name"].startswith("pipeline-network-") for item in remaining):
                raise RuntimeError("optional package uninstall retained its authority")
        evidence["application_modes"] = self.application_modes()
        return network.save("network-installation-matrix", evidence)

    def application_modes(self):
        from pipeline_network_fixture import external_ingress
        from pipeline_network_ingress_acceptance import IngressAcceptance
        from pipeline_network_probes import DNS

        network, delivery, proof = self.network, self.delivery, self.proof
        original = copy.deepcopy(network.current("python", "test")["desired"])
        namespace = delivery.prefix + "-python-test"
        before = delivery.release("python", "test")["document"]["observed"]
        identity = "package-modes-" + uuid4().hex
        evidence = {}
        try:
            for name, ingress, egress in (
                ("neither", False, False),
                ("ingress", True, False),
                ("egress", False, True),
                ("both", True, True),
            ):
                requested = copy.deepcopy(original)
                requested["ingress"]["enabled"], requested["egress"]["enabled"] = ingress, egress
                operation = network.apply("python", "test", requested, identity + name)
                health = delivery.route_health(delivery.release("python", "test"))
                external = (
                    external_ingress(proof, delivery, "python", "test")
                    if ingress
                    else IngressAcceptance(network).client("withdrawn")
                )
                deployments = json.loads(
                    proof.kube("-n", namespace, "get", "deployments", "-o", "json")
                )["items"]
                proxy = [item for item in deployments if item["metadata"]["name"] == "app-egress"]
                app = next(item for item in deployments if item["metadata"]["name"] == "app")
                names = {
                    item["name"]
                    for item in app["spec"]["template"]["spec"]["containers"][0].get("env", [])
                }
                if bool(proxy) != egress or ("HTTP_PROXY" in names) != egress:
                    raise RuntimeError("optional egress mode differs from the live application")
                outbound = None
                if egress:
                    outbound = json.loads(
                        proof.kube(
                            "-n", namespace, "exec", "deployment/app", "--", "python", "-c", DNS
                        )
                    )
                    if any(value != 200 for value in outbound.values()):
                        raise RuntimeError("enabled egress mode cannot reach approved upstreams")
                after = delivery.release("python", "test")["document"]["observed"]
                if (before["image"], before["helm_revision"]) != (
                    after["image"],
                    after["helm_revision"],
                ):
                    raise RuntimeError(
                        "optional networking mode rebuilt or redeployed the application"
                    )
                evidence[name] = {
                    "operation": operation,
                    "health": health,
                    "external": external,
                    "outbound": outbound,
                    "deployments": deployments,
                }
        finally:
            network.apply("python", "test", original, identity + "-restore")
        return evidence

    def existing(self):
        # Clone the owned gateway configuration into a separate operator-owned Gateway.
        # The package consumes that identity and must leave it untouched when detached.
        network, proof, delivery = self.network, self.proof, self.delivery
        values = json.loads((delivery.output / "networking-values.json").read_text())
        binding = values["policy"]["gateways"]["test"]
        gateway = json.loads(
            proof.kube("-n", binding["namespace"], "get", "gateway", binding["name"], "-o", "json")
        )
        identity = "existing-" + uuid4().hex[:8]
        proxy = json.loads(
            proof.kube(
                "-n",
                binding["namespace"],
                "get",
                "envoyproxy",
                gateway["spec"]["infrastructure"]["parametersRef"]["name"],
                "-o",
                "json",
            )
        )
        proof.kube(
            "create",
            "-f",
            "-",
            document={
                "apiVersion": proxy["apiVersion"],
                "kind": "EnvoyProxy",
                "metadata": {
                    "name": identity,
                    "namespace": binding["namespace"],
                    "labels": {"goblin-king.io/acceptance": proof.state["run_id"]},
                },
                "spec": proxy["spec"],
            },
        )
        external = {
            "apiVersion": gateway["apiVersion"],
            "kind": "Gateway",
            "metadata": {
                "name": identity,
                "namespace": binding["namespace"],
                "labels": {"goblin-king.io/acceptance": proof.state["run_id"]},
            },
            "spec": copy.deepcopy(gateway["spec"]),
        }
        external["spec"]["infrastructure"]["parametersRef"]["name"] = identity
        proof.kube("create", "-f", "-", document=external)
        proof.kube(
            "-n",
            binding["namespace"],
            "wait",
            "gateway/" + identity,
            "--for=condition=Programmed",
            "--timeout=90s",
        )
        observed = json.loads(
            proof.kube("-n", binding["namespace"], "get", "gateway", identity, "-o", "json")
        )
        selected = copy.deepcopy(values)
        services = json.loads(
            proof.kube(
                "get",
                "services",
                "-A",
                "-l",
                "gateway.envoyproxy.io/owning-gateway-name="
                + identity
                + ",gateway.envoyproxy.io/owning-gateway-namespace="
                + binding["namespace"],
                "-o",
                "json",
            )
        )["items"]
        if len(services) != 1:
            raise RuntimeError("existing Gateway endpoint identity is ambiguous")
        selected["policy"]["gateways"]["existing"] = binding | {
            "name": identity,
            "mode": "existing",
            "probe_address": services[0]["spec"]["clusterIP"],
            "dataplane_namespace": services[0]["metadata"]["namespace"],
            "pod_selector": {
                "gateway.envoyproxy.io/owning-gateway-name": identity,
                "gateway.envoyproxy.io/owning-gateway-namespace": binding["namespace"],
            },
        }
        selected["policy"]["releases"][delivery.prefix + "-python-test/app"]["gateways"].append(
            "existing"
        )
        # Existing-mode preflight checks actual controller and listener ownership.
        preflight = NetworkInstallation(proof.state["context"]).preflight(selected)
        path = network.output / "existing-gateway-values.json"
        path.write_text(json.dumps(selected, indent=2))
        rendered = proof.command(
            ["helm", "template", "existing-proof", "charts/pipeline-networking", "-f", str(path)]
        )
        import yaml

        if any(
            item
            and item["kind"] in {"Gateway", "EnvoyProxy", "GatewayClass", "Secret"}
            and item["metadata"]["name"] == identity
            for item in yaml.safe_load_all(rendered)
        ):
            raise RuntimeError("existing binding attempts to manage external gateway ownership")
        configurations = {
            (implementation, environment): copy.deepcopy(
                network.current(implementation, environment)["desired"]
            )
            for implementation in ("python", "node")
            for environment in ("test", "prod")
        }
        configurations["python", "test"]["ingress"]["gateway"] = "existing"
        main = json.loads(
            proof.command(
                [
                    "helm",
                    "--kube-context",
                    proof.state["context"],
                    "get",
                    "values",
                    "delivery",
                    "-n",
                    delivery.control,
                    "-o",
                    "json",
                ]
            )
        )
        main["pipeline"]["settings"]["networking"] = selected["policy"]
        main["pipeline"]["workerConfig"]["networking"] = selected["policy"]
        (delivery.output / "networking-values.json").write_text(json.dumps(selected, indent=2))
        (delivery.output / "values.json").write_text(json.dumps(main, indent=2))
        proof.command(
            [
                "helm",
                "--kube-context",
                proof.state["context"],
                "upgrade",
                "networking",
                "charts/pipeline-networking",
                "-n",
                delivery.prefix + "-ingress",
                "-f",
                str(path),
                "--wait",
                "--timeout=180s",
            ],
            timeout=210,
        )
        proof.command(
            [
                "helm",
                "--kube-context",
                proof.state["context"],
                "upgrade",
                "delivery",
                "charts/goblin-king",
                "-n",
                delivery.control,
                "-f",
                str(delivery.output / "values.json"),
                "--wait",
                "--timeout=180s",
            ],
            timeout=210,
        )
        operations = [
            network.apply(
                implementation,
                environment,
                configuration,
                identity + "-approve-" + implementation + environment,
            )
            for (implementation, environment), configuration in configurations.items()
        ]
        from pipeline_network_fixture import external_ingress

        health = external_ingress(proof, delivery, "python", "test")
        current = json.loads(
            proof.kube("-n", binding["namespace"], "get", "gateway", identity, "-o", "json")
        )
        if (
            current["metadata"]["uid"] != observed["metadata"]["uid"]
            or current["spec"] != observed["spec"]
        ):
            raise RuntimeError("existing Gateway identity or configuration changed")
        proof.state["existing_gateway"] = {
            "namespace": binding["namespace"],
            "name": identity,
            "uid": observed["metadata"]["uid"],
            "spec": observed["spec"],
        }
        proof.save()
        return network.save(
            "network-existing-gateway",
            {
                "gateway": observed,
                "preflight": preflight,
                "ownership_preserved": True,
                "health": health,
                "operations": operations,
            },
        )

    def cleanup(self):
        network, proof, delivery = self.network, self.proof, self.delivery
        before = NetworkInstallation(proof.state["context"]).api_inventory()
        operations = []
        for implementation in ("python", "node"):
            for environment in ("test", "prod"):
                configuration = copy.deepcopy(
                    network.current(implementation, environment)["desired"]
                )
                configuration["ingress"]["enabled"] = configuration["egress"]["enabled"] = False
                operations.append(
                    network.apply(
                        implementation,
                        environment,
                        configuration,
                        "cleanup-network-" + str(uuid4()),
                    )
                )
        proof.command(
            [
                "helm",
                "--kube-context",
                proof.state["context"],
                "uninstall",
                "networking",
                "-n",
                delivery.prefix + "-ingress",
                "--wait",
                "--timeout=90s",
            ]
        )
        after = NetworkInstallation(proof.state["context"]).api_inventory()
        if before != after:
            raise RuntimeError("networking uninstall changed external API ownership")
        existing = proof.state["existing_gateway"]
        gateway = json.loads(
            proof.kube(
                "-n", existing["namespace"], "get", "gateway", existing["name"], "-o", "json"
            )
        )
        if gateway["metadata"]["uid"] != existing["uid"] or gateway["spec"] != existing["spec"]:
            raise RuntimeError("networking uninstall changed externally owned Gateway state")
        removed = network.fixture.cleanup()
        proof.state["networking_cleanup"] = {"package_removed": True, "upstream": removed}
        proof.save()
        return network.save(
            "network-cleanup",
            {"operations": operations, "external_apis": after, "upstream": removed},
        )
