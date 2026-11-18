"""Install the pinned optional gateway controller or verify an existing installation."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path

from goblin_king.pipeline_networking import NetworkingPolicy

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_APIS = {
    "gatewayclasses.gateway.networking.k8s.io": "v1",
    "gateways.gateway.networking.k8s.io": "v1",
    "httproutes.gateway.networking.k8s.io": "v1",
    "envoyproxies.gateway.envoyproxy.io": "v1alpha1",
    "backendtrafficpolicies.gateway.envoyproxy.io": "v1alpha1",
}


class NetworkInstallation:
    def __init__(self, context):
        self.context = context

    def command(self, command, *, document=None):
        result = subprocess.run(
            command,
            input=json.dumps(document) if document else None,
            capture_output=True,
            text=True,
            timeout=330,
        )
        if result.returncode:
            raise RuntimeError(result.stderr[-3000:])
        return result.stdout

    def kube(self, *args, document=None):
        return self.command(["kubectl", "--context", self.context, *args], document=document)

    def api_inventory(self):
        items = json.loads(self.kube("get", "crds", "-o", "json"))["items"]
        return {
            item["metadata"]["name"]: {
                "uid": item["metadata"]["uid"],
                "spec_sha256": hashlib.sha256(
                    json.dumps(item["spec"], sort_keys=True).encode()
                ).hexdigest(),
                "served_versions": [v["name"] for v in item["spec"]["versions"] if v["served"]],
                "owner": item["metadata"].get("annotations", {}).get("meta.helm.sh/release-name"),
            }
            for item in items
            if item["spec"]["group"] in {"gateway.networking.k8s.io", "gateway.envoyproxy.io"}
        }

    def preflight(self, values):
        policy = NetworkingPolicy.model_validate(values["policy"])
        if not policy.enabled:
            return {"enabled": False, "required_apis": []}
        evidence = {"enabled": True, "policy_revision": policy.revision, "gateways": {}}
        if policy.ingress_available:
            apis = self.api_inventory()
            for name, version in REQUIRED_APIS.items():
                if version not in apis.get(name, {}).get("served_versions", []):
                    raise ValueError(
                        "required networking API is unavailable: " + name + "/" + version
                    )
            evidence["apis"] = apis
            gateway_class = json.loads(
                self.kube("get", "gatewayclass", values["gatewayClassName"], "-o", "json")
            )
            if gateway_class["spec"]["controllerName"] != values["controllerName"]:
                raise ValueError("GatewayClass belongs to another controller")
            if not any(
                c["type"] == "Accepted" and c["status"] == "True"
                for c in gateway_class.get("status", {}).get("conditions", [])
            ):
                raise ValueError("GatewayClass is not accepted by its controller")
            evidence["gateway_class"] = gateway_class
            for alias, binding in policy.gateways.items():
                if binding.controller_name != values["controllerName"]:
                    raise ValueError(
                        "gateway binding controller differs from the installed controller"
                    )
                if binding.mode == "existing":
                    gateway = json.loads(
                        self.kube(
                            "-n", binding.namespace, "get", "gateway", binding.name, "-o", "json"
                        )
                    )
                    if gateway["spec"]["gatewayClassName"] != values["gatewayClassName"]:
                        raise ValueError("existing gateway has an unapproved GatewayClass")
                    listeners = {item["name"]: item for item in gateway["spec"]["listeners"]}
                    if (
                        listeners.get(binding.listener, {}).get("protocol") != "HTTPS"
                        or listeners.get(binding.http_listener, {}).get("protocol") != "HTTP"
                    ):
                        raise ValueError(
                            "existing gateway requires approved HTTP and HTTPS listeners"
                        )
                    evidence["gateways"][alias] = gateway
        if values.get("certificateRequests"):
            available = json.loads(
                self.kube(
                    "get", "crd", "certificates.cert-manager.io", "--ignore-not-found", "-o", "json"
                )
                or "null"
            )
            if not available or not any(
                v["name"] == "v1" and v["served"] for v in available["spec"]["versions"]
            ):
                raise ValueError("certificate issuance requires an existing cert-manager v1 API")
            for request in values["certificateRequests"]:
                issuer = values.get("approvedIssuers", {}).get(request["issuer"])
                if not issuer or issuer["kind"] not in {"Issuer", "ClusterIssuer"}:
                    raise ValueError("certificate issuer is not approved")
                namespace = policy.gateways[request["gateway"]].namespace
                current = json.loads(
                    self.kube(
                        "-n",
                        namespace,
                        "get",
                        issuer["kind"],
                        issuer["name"],
                        "--ignore-not-found",
                        "-o",
                        "json",
                    )
                    or "null"
                )
                if not current or not any(
                    c["type"] == "Ready" and c["status"] == "True"
                    for c in current.get("status", {}).get("conditions", [])
                ):
                    raise ValueError("approved certificate issuer is unavailable or not ready")
        return evidence

    def install_controller(self, namespace, release, controller_name, gateway_class, output):
        for name in (namespace, release, gateway_class):
            if not re.fullmatch(r"[a-z][a-z0-9-]{0,50}[a-z0-9]", name):
                raise ValueError("installation names must be short DNS labels")
        dependencies = json.loads(
            (ROOT / "examples/pipeline/networking-dependencies.json").read_text()
        )
        existing = json.loads(
            self.command(["helm", "--kube-context", self.context, "list", "-A", "-o", "json"])
        )
        if any(item["name"] == release and item["namespace"] == namespace for item in existing):
            raise ValueError(
                "controller release already exists; use preflight instead of adopting it"
            )
        if self.kube("get", "namespace", namespace, "--ignore-not-found", "-o", "name").strip():
            raise ValueError("controller installation requires a new dedicated namespace")
        before = self.api_inventory()
        if before and self.kube(
            "get", "gatewayclass", gateway_class, "--ignore-not-found", "-o", "name"
        ).strip():
            raise ValueError("GatewayClass already exists; its ownership must be preserved")
        if before and any(
            version not in before.get(name, {}).get("served_versions", [])
            for name, version in REQUIRED_APIS.items()
        ):
            raise ValueError("existing networking APIs are incomplete; no CRDs were changed")
        values = {
            "crds": {"enabled": not bool(before)},
            "config": {"envoyGateway": {"gateway": {"controllerName": controller_name}}},
            "global": {
                "images": {
                    "envoyGateway": {"image": dependencies["gateway_image"]},
                    "envoyProxy": {"image": dependencies["envoy_image"]},
                }
            },
        }
        output.mkdir(parents=True, exist_ok=True)
        path = output / "controller-values.json"
        path.write_text(json.dumps(values, indent=2))
        chart = dependencies["gateway_chart"]
        self.command(
            [
                "helm",
                "--kube-context",
                self.context,
                "install",
                release,
                chart["reference"] + "@" + chart["digest"],
                "-n",
                namespace,
                "--create-namespace",
                "-f",
                str(path),
                "--wait",
                "--timeout=300s",
            ]
        )
        self.kube(
            "create",
            "-f",
            "-",
            document={
                "apiVersion": "gateway.networking.k8s.io/v1",
                "kind": "GatewayClass",
                "metadata": {
                    "name": gateway_class,
                    "labels": {"goblin-king.io/network-package": release},
                },
                "spec": {"controllerName": controller_name},
            },
        )
        self.kube(
            "wait", "gatewayclass/" + gateway_class, "--for=condition=Accepted", "--timeout=90s"
        )
        after = self.api_inventory()
        if any(after.get(name) != entry for name, entry in before.items()):
            raise RuntimeError("existing CRD identity or schema changed")
        report = {
            "namespace": namespace,
            "release": release,
            "gateway_class": gateway_class,
            "controller_name": controller_name,
            "dependencies": dependencies,
            "existing_apis": before,
            "installed_apis": after,
            "namespace_uid": json.loads(self.kube("get", "namespace", namespace, "-o", "json"))[
                "metadata"
            ]["uid"],
        }
        (output / "controller-installation.json").write_text(json.dumps(report, indent=2))
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("preflight", "install-controller"))
    parser.add_argument("--context", required=True)
    parser.add_argument("--values", type=Path)
    parser.add_argument("--namespace")
    parser.add_argument("--release")
    parser.add_argument("--controller-name")
    parser.add_argument("--gateway-class")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    installer = NetworkInstallation(args.context)
    if args.command == "preflight":
        if not args.values:
            parser.error("preflight requires --values")
        report = installer.preflight(json.loads(args.values.read_text()))
    else:
        if not all(
            (args.namespace, args.release, args.controller_name, args.gateway_class, args.output)
        ):
            parser.error(
                "install-controller requires namespace, release, controller-name, "
                "gateway-class and output"
            )
        report = installer.install_controller(
            args.namespace, args.release, args.controller_name, args.gateway_class, args.output
        )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
