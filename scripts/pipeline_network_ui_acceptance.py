"""Run the networking forms in an isolated headless browser and compare authenticated CLI state."""

from __future__ import annotations

import hashlib
import io
import json
import subprocess
import tarfile
import time
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
BROWSER = (
    "goblin-king-headless-cluster-audit@sha256:"
    "8dcf493a2ed34216f866df3b07d6baefb3c0044faa3468413cd07d7966879387"
)


def browser(network):
    delivery, proof = network.delivery, network.proof
    output = network.output / "admin-ui"
    output.mkdir(exist_ok=True)
    name = "network-ui-" + uuid4().hex[:10]
    release = delivery.release("python", "test")
    configuration = {
        "project": delivery.project,
        "release_key": release["key"],
        "revision": proof.state["images"]["controller"]["source_revision"],
        "acceptance_run_id": proof.state["run_id"],
    }
    proof.apply(
        {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {"name": name, "namespace": delivery.control},
            "data": {
                "config.json": json.dumps(configuration),
                "networking.cjs": (ROOT / "scripts/pipeline_network_ui.cjs").read_text(),
            },
        }
    )
    proof.apply(
        {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "NetworkPolicy",
            "metadata": {"name": name, "namespace": delivery.control},
            "spec": {
                "podSelector": {"matchLabels": {"goblin-king.io/ui-audit": name}},
                "policyTypes": ["Ingress", "Egress"],
                "ingress": [],
                "egress": [
                    {
                        "to": [
                            {
                                "podSelector": {
                                    "matchLabels": {"app.kubernetes.io/component": "admin"}
                                }
                            }
                        ],
                        "ports": [{"protocol": "TCP", "port": 8080}],
                    },
                    {
                        "to": [
                            {
                                "namespaceSelector": {
                                    "matchLabels": {"kubernetes.io/metadata.name": "kube-system"}
                                },
                                "podSelector": {"matchLabels": {"k8s-app": "kube-dns"}},
                            }
                        ],
                        "ports": [{"protocol": "TCP", "port": 53}, {"protocol": "UDP", "port": 53}],
                    },
                ],
            },
        }
    )
    pod = {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": name,
            "namespace": delivery.control,
            "labels": {"goblin-king.io/ui-audit": name},
        },
        "spec": {
            "restartPolicy": "Never",
            "automountServiceAccountToken": False,
            "activeDeadlineSeconds": 3600,
            "nodeSelector": {"kubernetes.io/hostname": "desktop-worker"},
            "securityContext": {
                "runAsNonRoot": True,
                "runAsUser": 1000,
                "runAsGroup": 1000,
                "fsGroup": 1000,
                "seccompProfile": {"type": "RuntimeDefault"},
            },
            "containers": [
                {
                    "name": "browser",
                    "image": BROWSER,
                    "imagePullPolicy": "IfNotPresent",
                    "command": ["node", "/inputs/networking.cjs"],
                    "env": [
                        {
                            "name": "GOBLIN_KING_AUDIT_TOKEN",
                            "valueFrom": {
                                "secretKeyRef": {"name": "pipeline-auth", "key": "api-token"}
                            },
                        }
                    ],
                    "resources": {
                        "requests": {"cpu": "100m", "memory": "256Mi"},
                        "limits": {"cpu": "1", "memory": "1Gi"},
                    },
                    "securityContext": {
                        "allowPrivilegeEscalation": False,
                        "capabilities": {"drop": ["ALL"]},
                    },
                    "volumeMounts": [
                        {"name": "inputs", "mountPath": "/inputs", "readOnly": True},
                        {"name": "proof", "mountPath": "/proof"},
                        {"name": "shm", "mountPath": "/dev/shm"},
                    ],
                }
            ],
            "volumes": [
                {"name": "inputs", "configMap": {"name": name}},
                {"name": "proof", "emptyDir": {"sizeLimit": "128Mi"}},
                {"name": "shm", "emptyDir": {"medium": "Memory", "sizeLimit": "128Mi"}},
            ],
        },
    }
    observed = json.loads(proof.kube("create", "-f", "-", "-o", "json", document=pod))
    (output / "pod.json").write_text(json.dumps(observed, indent=2))
    proof.kube(
        "-n", delivery.control, "wait", "--for=condition=Ready", "pod/" + name, "--timeout=120s"
    )
    deadline = time.monotonic() + 3000
    command = ["kubectl", "--context", proof.state["context"], "-n", delivery.control]
    while True:
        ready = subprocess.run(
            command
            + [
                "exec",
                name,
                "--",
                "sh",
                "-c",
                "test -f /proof/result.json || test -f /proof/error.txt",
            ],
            capture_output=True,
            timeout=30,
        )
        if ready.returncode == 0:
            break
        if time.monotonic() > deadline:
            raise RuntimeError("headless networking audit timed out; evidence Pod retained")
        time.sleep(3)
    raw = subprocess.check_output(
        command + ["exec", name, "--", "tar", "-cf", "-", "-C", "/proof", "."], timeout=60
    )
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        archive.extractall(output, filter="data")
    if (output / "error.txt").exists():
        raise RuntimeError((output / "error.txt").read_text()[-2500:])
    result = json.loads((output / "result.json").read_text())
    if not result["completed"] or len(result["inspected"]) != 4 or len(result["operations"]) != 4:
        raise RuntimeError("headless networking audit is incomplete")
    script = """
import json,os,subprocess,sys
a=json.load(sys.stdin)
env=os.environ|{'GOBLIN_KING_API_TOKEN':os.environ['GOBLIN_KING_BOOTSTRAP_ADMIN_TOKEN']}
result={}
for name,args in [('inspect',[a['release_key']]),('diagnostics',[a['project']])]:
 result[name]=json.loads(subprocess.check_output(['goblin-king','pipeline','networking',name,*args],env=env))
print(json.dumps(result))
"""
    cli = json.loads(
        proof.kube(
            "-n",
            delivery.control,
            "exec",
            "-i",
            "deploy/delivery-api",
            "--",
            "python",
            "-c",
            script,
            document=configuration,
        )
    )
    if cli["inspect"] != result["final"] or cli["diagnostics"] != result["diagnostics"]:
        raise RuntimeError("UI, API and CLI networking observations differ")
    screenshots = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in output.glob("*.png")
    }
    proof.kube(
        "delete",
        "--raw",
        f"/api/v1/namespaces/{delivery.control}/pods/{name}",
        "-f",
        "-",
        document={
            "apiVersion": "v1",
            "kind": "DeleteOptions",
            "preconditions": {"uid": observed["metadata"]["uid"]},
        },
    )
    proof.kube("-n", delivery.control, "delete", "configmap,networkpolicy", name)
    return network.save(
        "network-admin-api-cli",
        {
            "result": result,
            "cli": cli,
            "screenshots": screenshots,
            "browser_pod_uid": observed["metadata"]["uid"],
        },
    )
