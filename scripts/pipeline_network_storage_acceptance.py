"""Verify network boundaries while the SQLite node is unavailable and after fenced restore."""

from __future__ import annotations

import json
from uuid import uuid4

from pipeline_network_fixture import external_ingress
from pipeline_storage_acceptance import prove_storage_recovery

PROBE = r"""
import json,socket,sys,urllib.request
a=json.load(sys.stdin)
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
                    "seccompProfile": {"type": "RuntimeDefault"},
                },
                "containers": [
                    {
                        "name": "probe",
                        "image": release["image"],
                        "command": ["python", "-c", "import time;time.sleep(3600)"],
                        "securityContext": {
                            "allowPrivilegeEscalation": False,
                            "capabilities": {"drop": ["ALL"]},
                        },
                        "resources": {
                            "requests": {"cpu": "5m", "memory": "16Mi"},
                            "limits": {"cpu": "100m", "memory": "64Mi"},
                        },
                    }
                ],
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
            }
            self.path.write_text(json.dumps(observations, indent=2))

        try:
            result = prove_storage_recovery(delivery, observe)
        finally:
            current = json.loads(proof.kube("-n", namespace, "get", "pod", name, "-o", "json"))
            if current["metadata"]["uid"] != identity["metadata"]["uid"]:
                raise RuntimeError("network outage observer identity changed")
            proof.kube("-n", namespace, "delete", "pod", name, "--wait=true", "--timeout=60s")
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
        }
        if len(nodes) < 2 or any(
            row["connection"]["direct"] != "denied" for row in evidence.values()
        ):
            raise RuntimeError("network node loss did not preserve the boundary across both nodes")
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
