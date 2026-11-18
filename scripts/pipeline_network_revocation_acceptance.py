"""Close established proxy, direct and ingress connections when permissions are revoked."""

from __future__ import annotations

import copy
import json
import subprocess
import time
from uuid import uuid4

from pipeline_network_fixture import external_ingress

SOURCE = r"""
import http.client,json,socket,ssl,sys
a=json.load(sys.stdin)
connection=http.client.HTTPSConnection('app-egress',3128,
 context=ssl.create_default_context(cadata=a['ca']),timeout=10)
connection.set_tunnel('allowed.fixture.example.test',8443)
connection.request('GET','/stream');response=connection.getresponse()
assert response.status==200 and response.read(4)==b'live'
direct=socket.create_connection((a['address'],9002),timeout=10)
direct.sendall(b'live');assert direct.recv(4)==b'live'
print(json.dumps({'connect':'established','direct':'established'}),flush=True)
while True:
 assert response.read(4)==b'live'
 direct.sendall(b'live');assert direct.recv(4)==b'live'
"""

WEBSOCKET = r"""
import base64,json,os,socket,ssl,sys,time
a=json.loads(sys.argv[1]);ready=False
def read(stream,size):
 data=b''
 while len(data)<size:
  chunk=stream.recv(size-len(data))
  if not chunk:raise EOFError('connection closed')
  data+=chunk
 return data
try:
 with socket.create_connection((a['address'],a['port']),timeout=10) as raw:
  context=ssl.create_default_context(cadata=a['ca'])
  with context.wrap_socket(raw,server_hostname=a['hostname']) as s:
   key=base64.b64encode(os.urandom(16)).decode()
   s.sendall(('GET /ws HTTP/1.1\r\nHost: '+a['hostname']+'\r\nUpgrade: websocket\r\n'
     'Connection: Upgrade\r\nSec-WebSocket-Version: 13\r\n'
     'Sec-WebSocket-Key: '+key+'\r\n\r\n').encode())
   headers=b''
   while b'\r\n\r\n' not in headers:
    headers+=read(s,1);assert len(headers)<8192
   assert headers.startswith(b'HTTP/1.1 101'),headers
   started=time.monotonic()
   while time.monotonic()-started<600:
    body=b'live';mask=os.urandom(4)
    s.sendall(bytes([129,132])+mask+bytes(x^mask[i%4] for i,x in enumerate(body)))
    assert read(s,2)==bytes([129,4]);assert read(s,4)==body
    if not ready:
     ready=True;print(json.dumps({'established':True,'time':time.time()}),flush=True)
    time.sleep(1)
   raise TimeoutError('revocation did not close the WebSocket')
except (OSError,EOFError,AssertionError) as error:
 print(json.dumps({'closed':ready,'reason':type(error).__name__,'time':time.time()}),flush=True)
 if not ready or isinstance(error,TimeoutError):raise
"""

DENIED = r"""
import json,socket,sys,urllib.request,urllib.error
a=json.load(sys.stdin)
client=urllib.request.build_opener(urllib.request.ProxyHandler({'http':'http://app-egress:3128'}))
result={}
try:
 with client.open('http://allowed.fixture.example.test:8080/denied-probe',timeout=8) as r:
  result['proxy']=r.status
except urllib.error.HTTPError as e:result['proxy']=e.code
except urllib.error.URLError:result['proxy']='unavailable'
assert result['proxy'] in (403,502,503,504,'unavailable'),result
targets=[('bypass',8080)]+([('revoked-direct',9002)] if a.get('revoked_direct',True) else [])
for name,port in targets:
 try:
  with socket.create_connection((a['address'],port),timeout=3):result[name]='allowed'
 except OSError:result[name]='denied'
 assert result[name]=='denied',result
print(json.dumps(result))
"""


def revoke(network):
    delivery, proof = network.delivery, network.proof
    namespace = delivery.prefix + "-python-test"
    original = network.current("python", "test")["desired"]
    streaming = copy.deepcopy(original)
    streaming["ingress"].update(streaming=True, max_request_bytes=None)
    identity = "revoke-" + uuid4().hex[:12]
    network.apply("python", "test", streaming, identity + "-streaming")
    upstream = json.loads(network.fixture.path.read_text())
    args = upstream | {"ca": (network.fixture.output / "tls.crt").read_text()}
    pods = json.loads(
        proof.kube(
            "-n", namespace, "get", "pods", "-l", "app.kubernetes.io/instance=app", "-o", "json"
        )
    )["items"]
    app = next(
        item
        for item in pods
        if item["metadata"]["labels"].get("app.kubernetes.io/component") == "service"
    )
    endpoint = external_ingress(proof, delivery, "python", "test")
    external_args = {
        "address": endpoint["gateway_pods"][0]["status"]["hostIP"],
        "port": next(
            p["nodePort"] for p in endpoint["gateway_service"]["spec"]["ports"] if p["port"] == 443
        ),
        "hostname": f"python.test.{delivery.prefix}.example.test",
        "ca": (delivery.output / "test-gateway-ca.pem").read_text(),
    }
    client_name = "gk-" + identity
    client_id = network.fixture.docker(
        "run",
        "--detach",
        "--name",
        client_name,
        "--network",
        "kind",
        "--label",
        "goblin-king.io/acceptance=" + proof.state["run_id"],
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--memory=128m",
        "--pids-limit=64",
        "--entrypoint",
        "python",
        proof.state["images"]["controller"]["digest"],
        "-u",
        "-c",
        WEBSOCKET,
        json.dumps(external_args),
    ).strip()
    path = network.output / (identity + ".log")
    stream = path.open("w")
    process = subprocess.Popen(
        [
            "kubectl",
            "--context",
            proof.state["context"],
            "-n",
            namespace,
            "exec",
            "-i",
            app["metadata"]["name"],
            "--",
            "python",
            "-u",
            "-c",
            SOURCE,
        ],
        stdin=subprocess.PIPE,
        stdout=stream,
        stderr=subprocess.STDOUT,
        text=True,
    )
    process.stdin.write(json.dumps(args))
    process.stdin.close()
    try:
        delivery.wait(
            "established proxy and direct connections",
            lambda: '"established"' in path.read_text(),
            timeout=30,
        )
        delivery.wait(
            "established external WebSocket",
            lambda: '"established": true' in network.fixture.docker("logs", client_id),
            timeout=30,
        )
        if process.poll() is not None:
            raise RuntimeError("source connections closed before revocation")
        before = network.fixture.control()
        revoked = copy.deepcopy(streaming)
        revoked["egress"].update(hosts=[], direct=[])
        started = time.time()
        result = network.apply("python", "test", revoked, identity + "-deny")
        process.wait(timeout=30)

        def closed():
            lines = network.fixture.docker("logs", client_id).splitlines()
            events = [json.loads(line) for line in lines]
            return (
                events
                if any(item.get("closed") and item["time"] >= started for item in events)
                else None
            )

        websocket = delivery.wait("revoked WebSocket termination", closed, timeout=30)
        current = json.loads(
            proof.kube(
                "-n", namespace, "get", "pods", "-l", "app.kubernetes.io/instance=app", "-o", "json"
            )
        )["items"]
        old_uids = {item["metadata"]["uid"] for item in pods}
        if old_uids.intersection(item["metadata"]["uid"] for item in current):
            raise RuntimeError("revocation retained an application or proxy connection identity")
        denied = json.loads(
            proof.kube(
                "-n",
                namespace,
                "exec",
                "-i",
                "deployment/app",
                "--",
                "python",
                "-c",
                DENIED,
                document=args,
            )
        )
        after = network.fixture.control()
        if not all(
            any(event["kind"] == kind and event["time"] >= started for event in after["events"])
            for kind in ("tcp-close", "stream-closed")
        ):
            raise RuntimeError("upstream did not observe both revoked connections closing")
        return network.save(
            "network-revocation",
            {
                "operation": result,
                "old_pods": pods,
                "new_pods": current,
                "websocket": websocket,
                "source_exit_code": process.returncode,
                "denied": denied,
                "upstream_before": before,
                "upstream_after": after,
            },
        )
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=15)
        stream.close()
        inspected = json.loads(network.fixture.docker("inspect", client_id))[0]
        if (
            inspected["Id"] != client_id
            or inspected["Config"]["Labels"].get("goblin-king.io/acceptance")
            != proof.state["run_id"]
        ):
            raise RuntimeError("external connection client ownership changed")
        network.fixture.docker("rm", "--force", client_id)
        network.apply("python", "test", original, identity + "-restore")
