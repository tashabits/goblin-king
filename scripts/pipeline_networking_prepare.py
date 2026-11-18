"""Add an isolated optional networking package to a prepared local delivery fixture."""

from __future__ import annotations

import argparse
import base64
import datetime
import ipaddress
import json
import subprocess
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from goblin_king.pipeline_networking import NetworkingPolicy

ROOT = Path(__file__).resolve().parents[1]


def certificate(hostnames):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostnames[0])])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=1))
        .not_valid_after(now + datetime.timedelta(days=7))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName(name) for name in hostnames]), critical=False
        )
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM), key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )


def prepare(
    output,
    images,
    *,
    context,
    project,
    gateway_class,
    controller_name,
    resume=False,
    external_fixture=None,
):
    values_path = output / "values.json"
    values = json.loads(values_path.read_text())
    control = values["pipeline"]["workerConfig"]["api_url"].split("//", 1)[1].split(".")[1]
    if not control.endswith("-demo"):
        raise ValueError("only an isolated local delivery fixture may use this preparation")
    prefix = control.removesuffix("-demo")
    namespace = prefix + "-ingress"
    dependencies = json.loads((ROOT / "examples/pipeline/networking-dependencies.json").read_text())

    def kube(*args, document=None):
        result = subprocess.run(
            ["kubectl", "--context", context, *args],
            input=json.dumps(document) if document else None,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode:
            raise RuntimeError(
                "networking setup failed; submitted credential contents were not logged"
            )
        return result.stdout

    existing = json.loads(
        kube("get", "namespace", namespace, "--ignore-not-found", "-o", "json") or "null"
    )
    if existing and not resume:
        raise ValueError("networking namespace exists; inspect the existing installation")
    owner = json.loads(kube("get", "namespace", control, "-o", "json"))["metadata"].get(
        "labels", {}
    )
    labels = {key: value for key, value in owner.items() if key.startswith("goblin-king.io/")}
    labels["goblin-king.io/networking"] = "true"
    if existing and any(
        existing["metadata"].get("labels", {}).get(k) != v for k, v in labels.items()
    ):
        raise ValueError("networking namespace ownership differs from this fixture")
    if not existing:
        kube(
            "create",
            "-f",
            "-",
            document={
                "apiVersion": "v1",
                "kind": "Namespace",
                "metadata": {"name": namespace, "labels": labels},
            },
        )
    policy = {
        "enabled": True,
        "ingress_available": True,
        "egress_available": True,
        "proxy_image": dependencies["squid_image"],
        "gateways": {},
        "releases": {},
        "control_namespace": control,
    }
    nodes = json.loads(kube("get", "nodes", "-o", "json"))["items"]
    protected = set()
    for node in nodes:
        protected.update(node["spec"].get("podCIDRs", []))
        for address in node["status"]["addresses"]:
            if address["type"] in {"InternalIP", "ExternalIP"}:
                protected.add(str(ipaddress.ip_network(address["address"])))
    service_cidrs = json.loads(kube("get", "servicecidrs", "-o", "json"))["items"]
    for item in service_cidrs:
        protected.update(item["spec"]["cidrs"])
    if not service_cidrs or not all(node["spec"].get("podCIDRs") for node in nodes):
        raise ValueError("cluster service and pod address ranges are required for networking")
    policy["protected_cidrs"] = sorted(protected)
    for environment in ("test", "prod"):
        domains = [
            f"{implementation}.{environment}.{prefix}.example.test"
            for implementation in ("python", "node")
        ]
        secret = environment + "-tls"
        if existing:
            stored = json.loads(kube("-n", namespace, "get", "secret", secret, "-o", "json"))
            cert = base64.b64decode(stored["data"]["tls.crt"])
            if cert != (output / (environment + "-gateway-ca.pem")).read_bytes():
                raise ValueError("installed certificate differs from retained fixture identity")
        else:
            cert, key = certificate(domains)
            kube(
                "create",
                "-f",
                "-",
                document={
                    "apiVersion": "v1",
                    "kind": "Secret",
                    "metadata": {"name": secret, "namespace": namespace},
                    "type": "kubernetes.io/tls",
                    "data": {
                        "tls.crt": base64.b64encode(cert).decode(),
                        "tls.key": base64.b64encode(key).decode(),
                    },
                },
            )
        (output / (environment + "-gateway-ca.pem")).write_bytes(cert)
        policy["gateways"][environment] = {
            "name": environment,
            "controller_name": controller_name,
            "namespace": namespace,
            "mode": "managed",
            "environment": environment,
            "hostname_suffixes": [f"{environment}.{prefix}.example.test"],
            "exposure": "external",
            "certificates": {"local": secret},
            "dataplane_namespace": namespace,
            "pod_selector": {
                "gateway.envoyproxy.io/owning-gateway-name": environment,
                "gateway.envoyproxy.io/owning-gateway-namespace": namespace,
            },
        }
        for implementation in ("python", "node"):
            identity = f"{prefix}-{implementation}-{environment}/app"
            policy["releases"][identity] = {
                "project_id": project,
                "environment": environment,
                "gateways": [environment],
                "outbound_hosts": [
                    {"hostname": "example.com", "port": 80, "protocol": "HTTP"},
                    {"hostname": "example.com", "port": 443, "protocol": "HTTPS"},
                ],
            }
            if external_fixture:
                address = external_fixture["address"]
                entry = policy["releases"][identity]
                entry["outbound_hosts"] = [
                    {"hostname": name + ".fixture.example.test", "port": port, "protocol": protocol}
                    for name in ("allowed", "rebind", "mixed")
                    for port, protocol in ((8080, "HTTP"), (8443, "HTTPS"))
                ]
                entry["private_upstreams"] = {
                    name + ".fixture.example.test": [address]
                    for name in ("allowed", "rebind", "mixed")
                }
                entry["proxy_dns_servers"] = [address]
                entry["direct_destinations"] = [
                    {"cidr": str(ipaddress.ip_network(address)), "port": port, "protocol": protocol}
                    for port, protocol in ((9002, "TCP"), (9003, "UDP"))
                ]
    policy = NetworkingPolicy.model_validate(policy).model_dump(mode="json")
    package = {
        "policy": policy,
        "controlNamespace": control,
        "serviceType": "NodePort",
        "gatewayClassName": gateway_class,
        "controllerName": controller_name,
        "apiServiceAccount": "delivery-api",
        "apiServerCIDRs": [
            json.loads(kube("get", "service", "kubernetes", "-n", "default", "-o", "json"))["spec"][
                "clusterIP"
            ]
            + "/32",
            *[
                address["ip"] + "/32"
                for subset in json.loads(
                    kube("get", "endpoints", "kubernetes", "-n", "default", "-o", "json")
                )["subsets"]
                for address in subset["addresses"]
            ],
        ],
    }
    package_path = output / "networking-values.json"
    package_path.write_text(json.dumps(package, indent=2))

    def install_package():
        subprocess.run(
            [
                "helm",
                "--kube-context",
                context,
                "upgrade",
                "--install",
                "networking",
                str(ROOT / "charts/pipeline-networking"),
                "-n",
                namespace,
                "-f",
                str(package_path),
                "--wait",
                "--timeout",
                "180s",
            ],
            check=True,
            timeout=210,
        )

    install_package()
    for environment in ("test", "prod"):
        kube(
            "-n",
            namespace,
            "wait",
            "gateway/" + environment,
            "--for=condition=Programmed",
            "--timeout=90s",
        )
        services = json.loads(
            kube(
                "get",
                "services",
                "-A",
                "-l",
                "gateway.envoyproxy.io/owning-gateway-name="
                + environment
                + ",gateway.envoyproxy.io/owning-gateway-namespace="
                + namespace,
                "-o",
                "json",
            )
        )["items"]
        if len(services) != 1:
            raise ValueError("gateway does not have one attributable Service")
        policy["gateways"][environment]["probe_address"] = services[0]["spec"]["clusterIP"]
        policy["gateways"][environment]["dataplane_namespace"] = services[0]["metadata"][
            "namespace"
        ]
    values["pipeline"]["settings"]["networking"] = policy
    values["pipeline"]["settings"]["network_image"] = images["network"]["digest"]
    values["pipeline"]["workerConfig"]["networking"] = policy
    values["pipeline"]["workerConfig"]["network_image"] = images["network"]["digest"]
    values["pipeline"]["workerConfig"]["control_namespace"] = control
    discovery = json.loads(
        kube("-n", control, "get", "configmap", "pipeline-discovery", "-o", "json")
    )
    definitions = json.loads(discovery["data"]["goblins.json"])
    image_map = json.loads(discovery["data"]["images.json"])
    for environment in ("test", "prod"):
        kind = "pipeline.network." + environment
        if any(item["kind"] == kind for item in definitions["goblins"]) and not resume:
            raise ValueError("networking definitions already exist")
        definitions["goblins"] = [item for item in definitions["goblins"] if item["kind"] != kind]
        definitions["goblins"].append(
            {
                "kind": kind,
                "display_name": "Configure " + environment + " networking",
                "module": "goblin_king.pipeline_network_worker",
                "entrypoint": "configure",
                "timeout_seconds": 900,
                "max_retries": 0,
                "metadata": {"validation_input": {"validate": True}},
            }
        )
        image_map["workers"][kind] = {
            "image": images["network"]["digest"],
            "context": "workers/pipeline.network",
            "dockerfile": "Dockerfile",
        }
        values["scheduler"]["workloadSecurity"]["restricted"]["workerServiceAccounts"][kind] = (
            "pipeline-network-" + environment
        )
    discovery["data"] = {
        "goblins.json": json.dumps(definitions),
        "images.json": json.dumps(image_map),
    }
    discovery.pop("status", None)
    kube("replace", "-f", "-", document=discovery)
    values_path.write_text(json.dumps(values, indent=2))
    package["policy"] = policy
    package_path.write_text(json.dumps(package, indent=2))
    install_package()
    print(
        json.dumps(
            {
                "namespace": namespace,
                "policy_revision": NetworkingPolicy.model_validate(policy).revision,
                "scope": "local development fixture; public Internet exposure is not configured",
            }
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--images", type=Path, required=True)
    parser.add_argument("--context", default="docker-desktop")
    parser.add_argument("--project", default="acceptance")
    parser.add_argument("--gateway-class", required=True)
    parser.add_argument("--controller-name", required=True)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--external-fixture", type=Path)
    args = parser.parse_args()
    prepare(
        args.output.resolve(),
        json.loads(args.images.read_text()),
        context=args.context,
        project=args.project,
        gateway_class=args.gateway_class,
        controller_name=args.controller_name,
        resume=args.resume,
        external_fixture=json.loads(args.external_fixture.read_text())
        if args.external_fixture
        else None,
    )
