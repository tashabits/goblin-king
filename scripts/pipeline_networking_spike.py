"""Exercise pinned gateway and proxy capabilities without touching existing releases."""

from __future__ import annotations

import argparse
import base64
import datetime
import hashlib
import json
import subprocess
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from goblin_king.pipeline_network_resources import dns_rule, render_networking
from goblin_king.pipeline_networking import NetworkingPolicy

PROXY = "ubuntu/squid@sha256:6a097f68bae708cedbabd6188d68c7e2e7a38cedd05a176e1cc0ba29e3bbe029"
HELPER = "goblin-king@sha256:143ac2f5e2952241f9cfeb051cf9fe8c07bc28ff256cd389fbabe11789b292e3"
NAMESPACE = "gk-networking-spike-app"
GATEWAYS = "gk-networking-spike"
HOSTNAME = "api.apps.example.test"


def command(*args, document=None, timeout=120):
    result = subprocess.run(
        args,
        input=json.dumps(document) if document else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=timeout,
    )
    if result.returncode:
        raise RuntimeError(f"{args[0]} failed: {result.stderr[-2000:]}")
    return result.stdout


def kube(*args, document=None, timeout=120):
    return command(
        "kubectl",
        "--context",
        "docker-desktop",
        "--request-timeout=30s",
        *args,
        document=document,
        timeout=timeout,
    )


def apply(document):
    return kube("apply", "-f", "-", document=document)


def certificate():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, HOSTNAME)])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=1))
        .not_valid_after(now + datetime.timedelta(days=2))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(HOSTNAME)]), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM), key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )


def prepare(output):
    existing = json.loads(kube("get", "namespaces", "-o", "json"))["items"]
    if any(n["metadata"]["name"] == NAMESPACE for n in existing):
        raise ValueError("spike namespace exists; inspect it rather than adopting it")
    output.mkdir(parents=True, exist_ok=True)
    ca, private_key = certificate()
    (output / "ca.pem").write_bytes(ca)
    apply(
        {
            "apiVersion": "v1",
            "kind": "Namespace",
            "metadata": {"name": NAMESPACE, "labels": {"goblin-king.io/networking-spike": "true"}},
        }
    )
    # Private key goes directly to Kubernetes, never into command logs or local evidence.
    apply(
        {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {"name": "spike-tls", "namespace": GATEWAYS},
            "type": "kubernetes.io/tls",
            "data": {
                "tls.crt": base64.b64encode(ca).decode(),
                "tls.key": base64.b64encode(private_key).decode(),
            },
        }
    )
    apply(
        {
            "apiVersion": "gateway.networking.k8s.io/v1",
            "kind": "GatewayClass",
            "metadata": {"name": "gk-networking-spike"},
            "spec": {"controllerName": "goblin-king.io/networking-spike"},
        }
    )
    apply(
        {
            "apiVersion": "gateway.envoyproxy.io/v1alpha1",
            "kind": "EnvoyProxy",
            "metadata": {"name": "spike", "namespace": GATEWAYS},
            "spec": {
                "provider": {
                    "type": "Kubernetes",
                    "kubernetes": {"envoyService": {"type": "NodePort"}},
                }
            },
        }
    )
    apply(
        {
            "apiVersion": "gateway.networking.k8s.io/v1",
            "kind": "Gateway",
            "metadata": {"name": "spike", "namespace": GATEWAYS},
            "spec": {
                "gatewayClassName": "gk-networking-spike",
                "infrastructure": {
                    "parametersRef": {
                        "group": "gateway.envoyproxy.io",
                        "kind": "EnvoyProxy",
                        "name": "spike",
                    }
                },
                "listeners": [
                    {
                        "name": name,
                        "protocol": protocol,
                        "port": port,
                        "hostname": HOSTNAME,
                        "allowedRoutes": {
                            "namespaces": {
                                "from": "Selector",
                                "selector": {
                                    "matchLabels": {"kubernetes.io/metadata.name": NAMESPACE}
                                },
                            }
                        },
                        **(
                            {
                                "tls": {
                                    "mode": "Terminate",
                                    "certificateRefs": [{"name": "spike-tls"}],
                                }
                            }
                            if protocol == "HTTPS"
                            else {}
                        ),
                    }
                    for name, protocol, port in (("http", "HTTP", 80), ("https", "HTTPS", 443))
                ],
            },
        }
    )
    labels = {"app.kubernetes.io/instance": "fixture", "app.kubernetes.io/component": "service"}
    program = """from http.server import BaseHTTPRequestHandler,HTTPServer
class Handler(BaseHTTPRequestHandler):
 def do_GET(self):
  self.send_response(200); self.end_headers(); self.wfile.write(b'goblin-network-fixture')
HTTPServer(('0.0.0.0',8080),Handler).serve_forever()
"""
    apply(
        {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {"name": "fixture", "namespace": NAMESPACE},
            "spec": {
                "replicas": 1,
                "selector": {"matchLabels": labels},
                "template": {
                    "metadata": {"labels": labels},
                    "spec": {
                        "automountServiceAccountToken": False,
                        "containers": [
                            {
                                "name": "fixture",
                                "image": HELPER,
                                "command": ["python", "-c", program],
                                "securityContext": {
                                    "allowPrivilegeEscalation": False,
                                    "runAsUser": 65532,
                                    "capabilities": {"drop": ["ALL"]},
                                },
                                "resources": {
                                    "requests": {"cpu": "25m", "memory": "64Mi"},
                                    "limits": {"cpu": "250m", "memory": "128Mi"},
                                },
                                "readinessProbe": {"httpGet": {"path": "/", "port": 8080}},
                            }
                        ],
                    },
                },
            },
        }
    )
    apply(
        {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {"name": "fixture", "namespace": NAMESPACE},
            "spec": {"selector": labels, "ports": [{"port": 8080}]},
        }
    )
    apply(
        {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "NetworkPolicy",
            "metadata": {"name": "default-deny", "namespace": NAMESPACE},
            "spec": {
                "podSelector": {},
                "policyTypes": ["Ingress", "Egress"],
                "ingress": [],
                "egress": [dns_rule()],
            },
        }
    )
    policy = NetworkingPolicy.model_validate(
        {
            "enabled": True,
            "ingress_available": True,
            "egress_available": True,
            "proxy_image": PROXY,
            "gateways": {
                "spike": {
                    "name": "spike",
                    "namespace": GATEWAYS,
                    "environment": "test",
                    "hostname_suffixes": ["apps.example.test"],
                    "certificates": {"fixture": "spike-tls"},
                    "dataplane_namespace": GATEWAYS,
                    "pod_selector": {"gateway.envoyproxy.io/owning-gateway-name": "spike"},
                }
            },
            "releases": {
                NAMESPACE + "/fixture": {
                    "project_id": "fixture",
                    "environment": "test",
                    "gateways": ["spike"],
                    "outbound_hosts": [{"hostname": "example.com", "port": 80, "protocol": "HTTP"}],
                }
            },
        }
    )
    configuration = {
        "ingress": {
            "enabled": True,
            "gateway": "spike",
            "hostname": HOSTNAME,
            "certificate": "fixture",
        },
        "egress": {
            "enabled": True,
            "hosts": [{"hostname": "example.com", "port": 80, "protocol": "HTTP"}],
        },
    }
    resources = render_networking(
        configuration,
        policy,
        namespace=NAMESPACE,
        release="fixture",
        project_id="fixture",
        environment="test",
        operation_id="spike",
        active=True,
        port=8080,
    )
    (output / "rendered.json").write_text(json.dumps(resources, indent=2))
    (output / "policy.json").write_text(policy.model_dump_json(indent=2))
    for resource in resources:
        apply(resource)
    print(json.dumps({"prepared": NAMESPACE, "certificate_sha256": hashlib.sha256(ca).hexdigest()}))


def run(output):
    kube("-n", NAMESPACE, "rollout", "status", "deployment/fixture", "--timeout=120s", timeout=150)
    kube(
        "-n",
        NAMESPACE,
        "rollout",
        "status",
        "deployment/fixture-egress",
        "--timeout=120s",
        timeout=150,
    )
    services = json.loads(
        kube(
            "-n",
            GATEWAYS,
            "get",
            "services",
            "-l",
            "gateway.envoyproxy.io/owning-gateway-name=spike",
            "-o",
            "json",
        )
    )["items"]
    service = services[0]
    ports = {p["port"]: p["nodePort"] for p in service["spec"]["ports"]}
    gateway_pods = json.loads(
        kube(
            "-n",
            GATEWAYS,
            "get",
            "pods",
            "-l",
            "gateway.envoyproxy.io/owning-gateway-name=spike",
            "-o",
            "json",
        )
    )["items"]
    address = gateway_pods[0]["status"]["hostIP"]
    ca = (output / "ca.pem").read_text()
    probe = f"""import http.client,ssl,socket,json
original=socket.getaddrinfo
socket.getaddrinfo=lambda host,*a,**k: original({address!r} if host=={HOSTNAME!r} else host,*a,**k)
c=http.client.HTTPSConnection({HOSTNAME!r},{ports[443]},context=ssl.create_default_context(cadata={ca!r}),timeout=15)
c.request('GET','/',headers={{'Host':{HOSTNAME!r}}}); r=c.getresponse(); body=r.read().decode()
assert r.status==200 and body=='goblin-network-fixture',(r.status,body)
print(json.dumps({{'status':r.status,'body':body,'tls_verified':True}}))
"""
    ingress = json.loads(
        command(
            "docker",
            "run",
            "--rm",
            "--network",
            "kind",
            "--entrypoint",
            "python",
            HELPER,
            "-c",
            probe,
        )
    )
    outbound = """import urllib.request,urllib.error,json,socket
proxy=urllib.request.build_opener(urllib.request.ProxyHandler({'http':'http://fixture-egress:3128'}))
result={}
for name,url in [('allowed','http://example.com'),('denied','http://example.org'),('metadata','http://169.254.169.254')]:
 try:
  r=proxy.open(url,timeout=15); result[name]=r.status; r.close()
 except urllib.error.HTTPError as e: result[name]=e.code
assert result['allowed']==200,result
assert result['denied']==403 and result['metadata']==403,result
try:
 socket.create_connection(('1.1.1.1',80),timeout=3).close(); result['direct']='allowed'
except OSError: result['direct']='denied'
assert result['direct']=='denied',result
print(json.dumps(result))
"""
    egress = json.loads(
        kube("-n", NAMESPACE, "exec", "deployment/fixture", "--", "python", "-c", outbound)
    )
    observations = {
        "ingress": ingress,
        "egress": egress,
        "gateway": json.loads(kube("-n", GATEWAYS, "get", "gateway", "spike", "-o", "json")),
        "routes": json.loads(kube("-n", NAMESPACE, "get", "httproutes", "-o", "json")),
        "pods": json.loads(kube("-n", NAMESPACE, "get", "pods", "-o", "json")),
        "service": service,
    }
    (output / "observations.json").write_text(json.dumps(observations, indent=2))
    print(
        json.dumps(
            {"ingress": ingress, "egress": egress, "scope": "capability spike, not full acceptance"}
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("prepare", "run"))
    parser.add_argument("--output", type=Path, default=Path(".runlogs/pipeline-networking-spike"))
    args = parser.parse_args()
    (prepare if args.command == "prepare" else run)(args.output)
