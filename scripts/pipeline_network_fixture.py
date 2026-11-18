"""Owned Docker endpoints and independent TLS clients for cluster networking acceptance."""

from __future__ import annotations

import json
import secrets
import shutil
import subprocess
import time
from pathlib import Path

from pipeline_networking_prepare import certificate

ROOT = Path(__file__).resolve().parents[1]


class ExternalFixture:
    def __init__(self, output, identity, image):
        self.output = Path(output).resolve()
        self.identity, self.image = identity, image
        self.name = "gk-upstream-" + identity[:12]
        self.path = self.output / "external-fixture.json"

    def docker(self, *arguments, input=None):
        result = subprocess.run(
            ["docker", *arguments],
            input=input,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode:
            raise RuntimeError("external fixture Docker operation failed: " + result.stderr[-1000:])
        return result.stdout

    def inspect(self):
        item = json.loads(self.docker("inspect", self.name))[0]
        if item["Config"]["Labels"].get("goblin-king.io/acceptance") != self.identity:
            raise RuntimeError("external fixture ownership changed")
        if self.path.exists() and item["Id"] != json.loads(self.path.read_text())["container_id"]:
            raise RuntimeError("external fixture container was replaced")
        return item

    def prepare(self):
        self.output.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            self.inspect()
            return json.loads(self.path.read_text())
        existing = self.docker("ps", "-aq", "--filter", "name=^/" + self.name + "$").strip()
        if existing:
            raise RuntimeError("unrecorded external fixture exists; inspect before retrying")
        cert, key = certificate(
            [name + ".fixture.example.test" for name in ("allowed", "denied", "rebind", "mixed")]
        )
        (self.output / "tls.crt").write_bytes(cert)
        (self.output / "tls.key").write_bytes(key)
        (self.output / "control.env").write_text(
            "FIXTURE_ID=" + self.identity + "\nCONTROL_TOKEN=" + secrets.token_urlsafe(32) + "\n"
        )
        shutil.copyfile(ROOT / "examples/pipeline/network-upstream.py", self.output / "server.py")
        self.docker(
            "run",
            "--detach",
            "--name",
            self.name,
            "--network",
            "kind",
            "--label",
            "goblin-king.io/acceptance=" + self.identity,
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--user",
            "65532:65532",
            "--memory=128m",
            "--cpus=0.5",
            "--pids-limit=128",
            "--sysctl",
            "net.ipv4.ip_unprivileged_port_start=0",
            "--env-file",
            str(self.output / "control.env"),
            "--mount",
            "type=bind,source=" + str(self.output) + ",target=/fixture,readonly",
            "--entrypoint",
            "python",
            self.image,
            "/fixture/server.py",
        )
        item = self.inspect()
        state = {
            "container_id": item["Id"],
            "name": self.name,
            "identity": self.identity,
            "address": item["NetworkSettings"]["Networks"]["kind"]["IPAddress"],
            "image": self.image,
            "network": "kind",
            "published_ports": [],
        }
        self.path.write_text(json.dumps(state, indent=2))
        for _ in range(30):
            try:
                result = self.control()
                if result["fixture"] == self.identity:
                    return state
            except RuntimeError:
                pass
            time.sleep(1)
        raise RuntimeError("external fixture did not become ready")

    def control(self, updates=None):
        self.inspect()
        script = """
import json,os,sys
from urllib.request import Request,urlopen
updates=json.load(sys.stdin)
request=Request('http://127.0.0.1:9000/',
    data=json.dumps(updates).encode() if updates is not None else None,
    headers={'Authorization':'Bearer '+os.environ['CONTROL_TOKEN']})
with urlopen(request,timeout=10) as response: print(response.read().decode())
"""
        return json.loads(
            self.docker("exec", "-i", self.name, "python", "-c", script, input=json.dumps(updates))
        )

    def cleanup(self):
        item = self.inspect()
        self.docker("rm", "--force", item["Id"])
        return {"removed_container": item["Id"], "identity": self.identity}


def external_client(proof, delivery, implementation, environment, script, extra=None):
    selector = (
        "gateway.envoyproxy.io/owning-gateway-name="
        + environment
        + ",gateway.envoyproxy.io/owning-gateway-namespace="
        + delivery.prefix
        + "-ingress"
    )
    services = json.loads(proof.kube("get", "services", "-A", "-l", selector, "-o", "json"))[
        "items"
    ]
    pods = json.loads(proof.kube("get", "pods", "-A", "-l", selector, "-o", "json"))["items"]
    if len(services) != 1 or not pods:
        raise RuntimeError("gateway endpoint identity is ambiguous")
    service = services[0]
    ports = {entry["port"]: entry["nodePort"] for entry in service["spec"]["ports"]}
    address = pods[0]["status"]["hostIP"]
    hostname = f"{implementation}.{environment}.{delivery.prefix}.example.test"
    release = delivery.release(implementation, environment)["document"]["observed"]
    build = next(row for row in delivery.collection("builds") if row["id"] == release["build_id"])
    ca = (delivery.output / (environment + "-gateway-ca.pem")).read_text()
    arguments = {
        "address": address,
        "hostname": hostname,
        "ports": ports,
        "ca": ca,
        "implementation": implementation,
        "revision": build["document"]["request"]["source"]["commit"],
    }
    arguments.update(extra or {})
    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "-i",
            "--network",
            "kind",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--memory=128m",
            "--pids-limit=64",
            "--entrypoint",
            "python",
            proof.state["images"]["controller"]["digest"],
            "-c",
            script,
        ],
        input=json.dumps(arguments),
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode:
        raise RuntimeError("external ingress probe failed: " + result.stderr[-2000:])
    return {
        "client": json.loads(result.stdout),
        "gateway_service": service,
        "gateway_pods": pods,
        "scope": "Docker network outside Kubernetes; not public Internet",
    }


def external_ingress(proof, delivery, implementation, environment):
    script = """
import http.client,ssl,socket,json,sys
a=json.load(sys.stdin)
original=socket.getaddrinfo
socket.getaddrinfo=lambda host,*args,**kwargs: original(
    a['address'] if host==a['hostname'] else host,*args,**kwargs)
observed={}
cases=[('https',True,a['hostname']),('redirect',False,a['hostname']),
       ('unknown-host',True,'unapproved.example.test')]
for name,tls,host in cases:
 c=(http.client.HTTPSConnection(a['hostname'],a['ports']['443'],context=ssl.create_default_context(cadata=a['ca']),timeout=15)
    if tls else http.client.HTTPConnection(a['address'],a['ports']['80'],timeout=15))
 c.request('GET','/health',headers={'Host':host}); r=c.getresponse(); body=r.read(8192)
 observed[name]={'status':r.status,'location':r.getheader('Location'),'body':body.decode()}
 c.close()
assert observed['https']['status']==200,observed
body=json.loads(observed['https']['body'])
assert body['implementation']==a['implementation'] and body['revision']==a['revision'],body
assert observed['redirect']['status'] in (301,308),observed
assert observed['redirect']['location'].startswith('https://'+a['hostname']+'/'),observed
assert observed['unknown-host']['status'] in (404,421),observed
print(json.dumps({'observations':observed,'tls_verified':True,'source_revision':a['revision']}))
"""
    return external_client(proof, delivery, implementation, environment, script)
