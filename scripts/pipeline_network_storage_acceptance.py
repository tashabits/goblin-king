"""Verify network boundaries while the SQLite node is unavailable and after fenced restore."""

from __future__ import annotations

import json
from uuid import uuid4

from pipeline_network_fixture import external_ingress
from pipeline_storage_acceptance import prove_storage_recovery

PROBE = r"""
import json,socket,sys,urllib.request
a=json.load(sys.stdin)
with urllib.request.urlopen('http://127.0.0.1:8080/health',timeout=3) as response:
 application=json.load(response)
 assert response.status==200 and application['revision']==a['revision'],application
client=urllib.request.build_opener(urllib.request.ProxyHandler({'http':a['proxy']}))
try:
 with client.open('http://allowed.fixture.example.test:8080/node-loss',timeout=4) as response:
  result={'proxy':response.status,'body':json.load(response)}
except Exception as error: result={'proxy':'unavailable','error':type(error).__name__}
try:
 connection=socket.create_connection((a['address'],8080),timeout=3)
 connection.close();result['direct']='allowed'
except OSError: result['direct']='denied'
assert result['direct']=='denied',result
if a['phase']!='during':assert result['proxy']==200,result
result['application']=application
print(json.dumps(result))
"""


class NetworkStorageAcceptance:
    def __init__(self, network):
        self.network = network
        self.delivery, self.proof = network.delivery, network.proof
        self.path = network.output / "node-loss-observations.json"

    def storage(self):
        delivery, proof, network = self.delivery, self.proof, self.network
        namespace = delivery.prefix + "-python-test"
        name = "network-outage-observer-" + uuid4().hex[:8]
        release = delivery.release("python", "test")["document"]["observed"]
        upstream = json.loads(network.fixture.path.read_text())
        proxy = json.loads(
            proof.kube("-n", namespace, "get", "service", "app-egress", "-o", "json")
        )
        build = next(
            row for row in delivery.collection("builds") if row["id"] == release["build_id"]
        )
        revision = build["document"]["request"]["source"]["commit"]
        placement_path = network.output / "outage-proxy-placement.json"
        if placement_path.exists():
            placement = json.loads(placement_path.read_text())
        else:
            placement = json.loads(
                proof.kube("-n", namespace, "get", "deployment", "app-egress", "-o", "json")
            )
            placement_path.write_text(json.dumps(placement, indent=2))
        patch = [
            {"op": "test", "path": "/metadata/uid", "value": placement["metadata"]["uid"]},
            {
                "op": "add",
                "path": "/spec/template/spec/nodeSelector",
                "value": {"kubernetes.io/hostname": "desktop-control-plane"},
            },
            {
                "op": "add",
                "path": "/spec/template/spec/tolerations",
                "value": [{"operator": "Exists"}],
            },
        ]
        proof.kube(
            "-n",
            namespace,
            "patch",
            "deployment",
            "app-egress",
            "--type=json",
            "-p",
            json.dumps(patch),
        )
        proof.kube("-n", namespace, "rollout", "status", "deployment/app-egress", "--timeout=120s")
        pod = {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": {
                "name": name,
                "namespace": namespace,
                "labels": {
                    "app.kubernetes.io/instance": "app",
                    "app.kubernetes.io/component": "background",
                    "goblin-king.io/acceptance": proof.state["run_id"],
                },
            },
            "spec": {
                "nodeSelector": {"kubernetes.io/hostname": "desktop-control-plane"},
                "tolerations": [{"operator": "Exists"}],
                "automountServiceAccountToken": False,
                "imagePullSecrets": [{"name": "registry-pull"}],
                "restartPolicy": "Never",
                "activeDeadlineSeconds": 3600,
                "securityContext": {
                    "runAsNonRoot": True,
                    "runAsUser": 65532,
                    "fsGroup": 65532,
                    "seccompProfile": {"type": "RuntimeDefault"},
                },
                "containers": [
                    {
                        "name": "probe",
                        "image": release["image"],
                        "command": ["python", "/app/app.py"],
                        "env": [{"name": "SOURCE_REVISION", "value": revision}],
                        "readinessProbe": {"httpGet": {"path": "/health", "port": 8080}},
                        "volumeMounts": [{"name": "data", "mountPath": "/data"}],
                        "securityContext": {
                            "allowPrivilegeEscalation": False,
                            "readOnlyRootFilesystem": True,
                            "capabilities": {"drop": ["ALL"]},
                        },
                        "resources": {
                            "requests": {"cpu": "5m", "memory": "16Mi"},
                            "limits": {"cpu": "100m", "memory": "64Mi"},
                        },
                    }
                ],
                "volumes": [{"name": "data", "emptyDir": {"sizeLimit": "16Mi"}}],
            },
        }
        proof.apply(pod)
        proof.kube(
            "-n", namespace, "wait", "--for=condition=Ready", "pod/" + name, "--timeout=120s"
        )
        identity = json.loads(proof.kube("-n", namespace, "get", "pod", name, "-o", "json"))
        observations = json.loads(self.path.read_text()) if self.path.exists() else {}

        def observe(phase):
            observation = json.loads(
                proof.kube(
                    "-n",
                    namespace,
                    "exec",
                    "-i",
                    name,
                    "--",
                    "python",
                    "-c",
                    PROBE,
                    document={
                        "phase": phase,
                        "proxy": "http://" + proxy["spec"]["clusterIP"] + ":3128",
                        "address": upstream["address"],
                        "revision": revision,
                    },
                )
            )
            observations[phase] = {
                "connection": observation,
                "pod": identity,
                "application_pods": json.loads(
                    proof.kube(
                        "-n",
                        namespace,
                        "get",
                        "pods",
                        "-l",
                        "app.kubernetes.io/instance=app",
                        "-o",
                        "json",
                    )
                )["items"],
                "proxy_pods": json.loads(
                    proof.kube(
                        "get",
                        "pods",
                        "-A",
                        "-l",
                        "app.kubernetes.io/component=egress",
                        "-o",
                        "json",
                    )
                )["items"],
            }
            self.path.write_text(json.dumps(observations, indent=2))

        try:
            result = prove_storage_recovery(delivery, observe)
        finally:
            current = json.loads(proof.kube("-n", namespace, "get", "pod", name, "-o", "json"))
            if current["metadata"]["uid"] != identity["metadata"]["uid"]:
                raise RuntimeError("network outage observer identity changed")
            proof.kube("-n", namespace, "delete", "pod", name, "--wait=true", "--timeout=60s")
            current_proxy = json.loads(
                proof.kube("-n", namespace, "get", "deployment", "app-egress", "-o", "json")
            )
            patch = [{"op": "test", "path": "/metadata/uid", "value": placement["metadata"]["uid"]}]
            for field in ("nodeSelector", "tolerations"):
                original = placement["spec"]["template"]["spec"]
                if field in original:
                    patch.append(
                        {
                            "op": "add",
                            "path": "/spec/template/spec/" + field,
                            "value": original[field],
                        }
                    )
                elif field in current_proxy["spec"]["template"]["spec"]:
                    patch.append({"op": "remove", "path": "/spec/template/spec/" + field})
            proof.kube(
                "-n",
                namespace,
                "patch",
                "deployment",
                "app-egress",
                "--type=json",
                "-p",
                json.dumps(patch),
            )
            proof.kube(
                "-n", namespace, "rollout", "status", "deployment/app-egress", "--timeout=120s"
            )
        self.backup_restore()
        return result

    def node_loss(self):
        evidence = json.loads(self.path.read_text())
        if set(evidence) != {"before", "during", "after"}:
            raise RuntimeError("network node-loss evidence is incomplete")
        nodes = {
            pod["spec"]["nodeName"]
            for phase in evidence.values()
            for pod in phase["application_pods"]
            if pod["spec"].get("nodeName")
        }
        if len(nodes) < 2 or any(
            row["connection"]["direct"] != "denied" for row in evidence.values()
        ):
            raise RuntimeError("network node loss did not preserve the boundary across both nodes")
        proxy_nodes = {
            pod["spec"]["nodeName"]
            for phase in evidence.values()
            for pod in phase["proxy_pods"]
            if pod["metadata"]["namespace"].startswith(self.delivery.prefix + "-")
            and pod["status"]["phase"] == "Running"
        }
        if len(proxy_nodes) < 2:
            raise RuntimeError("network node loss did not exercise proxies on both nodes")
        return self.network.save("network-node-loss", evidence)

    def backup_restore(self):
        path = self.delivery.output / "storage" / "reconciliation.json"
        reconciliation = json.loads(path.read_text())
        verified = [row for row in reconciliation if row.get("networking")]
        if len(verified) != 4 or any(not row["networking"].get("observation") for row in verified):
            raise RuntimeError("restore did not independently verify all four network observations")
        health = {
            implementation + "-" + environment: external_ingress(
                self.proof, self.delivery, implementation, environment
            )
            for implementation in ("python", "node")
            for environment in ("test", "prod")
        }
        return self.network.save(
            "network-backup-restore", {"reconciliation": reconciliation, "external_health": health}
        )
