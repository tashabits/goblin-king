"""Install a TLS registry with scoped create-only publication and node trust.

This development installer owns only its namespace and one registry-specific
containerd trust directory per node. It never changes global TLS verification.
Credentials are generated in memory and saved only as Kubernetes Secrets.
"""

from __future__ import annotations

import argparse
import base64
import ipaddress
import json
import re
import secrets
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import bcrypt
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

ZOT_IMAGE = (
    "ghcr.io/project-zot/zot-linux-amd64@sha256:"
    "b68f727fe35c1eb45b2ae91519dfb0e33192d69b5104882c3e7809b7146c4a02"
)


def install(context, namespace, node_port, output, *, helper_image):
    if not re.fullmatch(r"[^\s]+@sha256:[0-9a-f]{64}", helper_image):
        raise ValueError("registry trust installation requires a digest-pinned helper image")

    def kube(*args, body=None):
        result = subprocess.run(
            ["kubectl", "--context", context, *args],
            input=json.dumps(body) if body else None,
            capture_output=True,
            text=True,
            timeout=180,
        )
        if result.returncode:
            # Never echo submitted Secret documents or kubectl validation diagnostics.
            raise RuntimeError(f"kubectl {' '.join(args[:3])} failed (exit {result.returncode})")
        return result.stdout

    def create(kind, name, spec=None, **fields):
        document = {
            "apiVersion": "v1",
            "kind": kind,
            "metadata": {"name": name, "namespace": namespace},
            **fields,
        }
        if spec is not None:
            document["spec"] = spec
        kube("create", "-f", "-", body=document)

    def secret(name, data, kind="Opaque"):
        create(
            "Secret",
            name,
            type=kind,
            data={
                k: base64.b64encode(v if isinstance(v, bytes) else v.encode()).decode()
                for k, v in data.items()
            },
        )

    nodes = json.loads(kube("get", "nodes", "-o", "json"))["items"]
    ips = [
        a["address"] for n in nodes for a in n["status"]["addresses"] if a["type"] == "InternalIP"
    ]
    host = f"{ips[0]}:{node_port}"
    kube(
        "create",
        "-f",
        "-",
        body={
            "apiVersion": "v1",
            "kind": "Namespace",
            "metadata": {"name": namespace, "labels": {"goblin-king.io/local-registry": "true"}},
        },
    )
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.now(UTC)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Goblin King local registry CA")])
    ca = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "registry")]))
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=30))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.IPAddress(ipaddress.ip_address(ip)) for ip in ips]
                + [
                    x509.DNSName(f"registry.{namespace}.svc"),
                    x509.DNSName("localhost"),
                    x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                ]
            ),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    ca_pem = ca.public_bytes(serialization.Encoding.PEM)
    secret(
        "registry-tls",
        {
            "tls.crt": cert.public_bytes(serialization.Encoding.PEM),
            "tls.key": key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ),
            "ca.crt": ca_pem,
        },
    )
    accounts = {
        name: secrets.token_urlsafe(32) for name in ("python-publish", "node-publish", "pull")
    }
    htpasswd = (
        "\n".join(
            f"{name}:{bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()}"
            for name, password in accounts.items()
        )
        + "\n"
    )
    secret("registry-auth", {"htpasswd": htpasswd})
    policy = {}
    for implementation in ("python", "node"):
        account = f"{implementation}-publish"
        policy[f"{implementation}/**"] = {
            "policies": [
                {"users": [account], "actions": ["read", "create"]},
                {"users": ["pull"], "actions": ["read"]},
            ],
            "defaultPolicy": [],
        }
        secret(account, {"username": account, "password": accounts[account], "ca.crt": ca_pem})
    auth = base64.b64encode(f"pull:{accounts['pull']}".encode()).decode()
    secret(
        "registry-pull",
        {".dockerconfigjson": json.dumps({"auths": {host: {"auth": auth}}})},
        "kubernetes.io/dockerconfigjson",
    )
    config = {
        "distSpecVersion": "1.1.0",
        "storage": {"rootDirectory": "/var/lib/registry"},
        "http": {
            "address": "0.0.0.0",
            "port": "5000",
            "tls": {"cert": "/tls/tls.crt", "key": "/tls/tls.key"},
            "auth": {"htpasswd": {"path": "/auth/htpasswd"}},
            "accessControl": {"repositories": policy},
        },
        "log": {"level": "warn"},
    }
    create(
        "ConfigMap",
        "registry-config",
        data={"config.json": json.dumps(config), "ca.crt": ca_pem.decode()},
    )
    create(
        "PersistentVolumeClaim",
        "registry-data",
        {"accessModes": ["ReadWriteOnce"], "resources": {"requests": {"storage": "10Gi"}}},
    )
    create(
        "Deployment",
        "registry",
        apiVersion="apps/v1",
        spec={
            "replicas": 1,
            "selector": {"matchLabels": {"app": "local-registry"}},
            "template": {
                "metadata": {"labels": {"app": "local-registry"}},
                "spec": {
                    "automountServiceAccountToken": False,
                    "securityContext": {"runAsUser": 1000, "runAsGroup": 1000, "fsGroup": 1000},
                    "containers": [
                        {
                            "name": "registry",
                            "image": ZOT_IMAGE,
                            "args": ["serve", "/config/config.json"],
                            "ports": [{"containerPort": 5000}],
                            "readinessProbe": {"tcpSocket": {"port": 5000}, "periodSeconds": 2},
                            "resources": {
                                "requests": {"cpu": "100m", "memory": "128Mi"},
                                "limits": {"cpu": "1", "memory": "512Mi"},
                            },
                            "securityContext": {
                                "allowPrivilegeEscalation": False,
                                "capabilities": {"drop": ["ALL"]},
                            },
                            "volumeMounts": [
                                {"name": n, "mountPath": p, "readOnly": n != "data"}
                                for n, p in (
                                    ("data", "/var/lib/registry"),
                                    ("config", "/config"),
                                    ("tls", "/tls"),
                                    ("auth", "/auth"),
                                )
                            ],
                        }
                    ],
                    "volumes": [
                        {"name": "data", "persistentVolumeClaim": {"claimName": "registry-data"}},
                        {"name": "config", "configMap": {"name": "registry-config"}},
                        {"name": "tls", "secret": {"secretName": "registry-tls"}},
                        {"name": "auth", "secret": {"secretName": "registry-auth"}},
                    ],
                },
            },
        },
    )
    create(
        "Service",
        "registry",
        {
            "type": "NodePort",
            "selector": {"app": "local-registry"},
            "ports": [{"port": 443, "targetPort": 5000, "nodePort": node_port}],
        },
    )
    script = (
        "from pathlib import Path; import shutil; "
        "config=Path('/containerd-config/config.toml').read_text(); "
        "assert '/etc/containerd/certs.d' in config, "
        "'unrecognized containerd trust configuration'; "
        "shutil.copyfile('/ca/ca.crt','/trust/ca.crt'); "
        f"Path('/trust/hosts.toml').write_text('server = \"https://{host}\"\\n"
        f'[host."https://{host}"]\\n  capabilities = ["pull", "resolve"]\\n'
        f'  ca = "/etc/containerd/certs.d/{host}/ca.crt"\\n\'); '
        "print('registry-specific CA installed'); import time; time.sleep(86400)"
    )
    create(
        "DaemonSet",
        "registry-trust",
        apiVersion="apps/v1",
        spec={
            "selector": {"matchLabels": {"app": "registry-trust"}},
            "template": {
                "metadata": {"labels": {"app": "registry-trust"}},
                "spec": {
                    "automountServiceAccountToken": False,
                    "tolerations": [{"operator": "Exists"}],
                    "containers": [
                        {
                            "name": "install",
                            "image": helper_image,
                            "imagePullPolicy": "IfNotPresent",
                            "command": ["python", "-c", script],
                            "securityContext": {
                                "runAsUser": 0,
                                "allowPrivilegeEscalation": False,
                                "capabilities": {"drop": ["ALL"]},
                            },
                            "volumeMounts": [
                                {"name": "trust", "mountPath": "/trust"},
                                {"name": "ca", "mountPath": "/ca", "readOnly": True},
                                {
                                    "name": "containerd",
                                    "mountPath": "/containerd-config/config.toml",
                                    "readOnly": True,
                                },
                            ],
                        }
                    ],
                    "volumes": [
                        {
                            "name": "trust",
                            "hostPath": {
                                "path": f"/etc/containerd/certs.d/{host}",
                                "type": "DirectoryOrCreate",
                            },
                        },
                        {"name": "ca", "configMap": {"name": "registry-config"}},
                        {
                            "name": "containerd",
                            "hostPath": {"path": "/etc/containerd/config.toml", "type": "File"},
                        },
                    ],
                },
            },
        },
    )
    kube("-n", namespace, "rollout", "status", "deployment/registry", "--timeout=120s")
    kube("-n", namespace, "rollout", "status", "daemonset/registry-trust", "--timeout=120s")
    observation = {
        "context": context,
        "namespace": namespace,
        "registry": host,
        "image": ZOT_IMAGE,
        "nodes": [n["metadata"]["name"] for n in nodes],
        "ca_expires": cert.not_valid_after_utc.isoformat(),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(observation, indent=2) + "\n")
    print(json.dumps(observation))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context", default="docker-desktop")
    parser.add_argument("--namespace", default="gk-local-registry")
    parser.add_argument("--node-port", type=int, default=30443)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--helper-image", required=True)
    args = parser.parse_args()
    install(
        args.context, args.namespace, args.node_port, args.output, helper_image=args.helper_image
    )
