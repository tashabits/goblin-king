"""TLS, request limits and route boundaries observed from outside Kubernetes."""

from __future__ import annotations

import base64
import copy
import datetime
import json
import subprocess
from uuid import uuid4

from pipeline_network_fixture import external_client
from pipeline_networking_prepare import certificate

CLIENT = r"""
import base64,hashlib,http.client,json,os,socket,ssl,sys,time
a=json.load(sys.stdin)
original=socket.getaddrinfo
socket.getaddrinfo=lambda host,*args,**kwargs: original(
    a['address'] if host==a['hostname'] else host,*args,**kwargs)
context=ssl.create_default_context(cadata=a['ca'])
def request(path,method='GET',body=None,headers=None):
 c=http.client.HTTPSConnection(a['hostname'],a['ports']['443'],context=context,timeout=12)
 start=time.monotonic()
 c.request(method,path,body=body,headers={'Host':a['hostname'],**(headers or {})})
 r=c.getresponse();data=r.read(8192)
 result={'status':r.status,'body':data.decode(errors='replace'),'seconds':time.monotonic()-start}
 c.close();return result
result={}
if a['mode']=='limits':
 result['healthy']=request('/health')
 result['small-body']=request('/health','POST',b'x'*512)
 result['large-body']=request('/health','POST',b'x'*2048)
 result['slow']=request('/slow')
 assert result['healthy']['status']==result['small-body']['status']==200,result
 assert result['large-body']['status']==413,result
 assert result['slow']['status']==504 and result['slow']['seconds']<4,result
elif a['mode']=='negative':
 for path in ['/not-approved','/admin/','/pipeline/webhooks/github']:
  result[path]=request(path);assert result[path]['status']==404,result
 result['unknown-host']=request('/health',headers={'Host':'unknown.example.test'})
 result['other-environment']=request('/health',headers={'Host':a['other_hostname']})
 assert all(result[x]['status'] in (404,421) for x in ('unknown-host','other-environment')),result
 result['forwarded']=request('/health',headers={'Forwarded':'for=127.0.0.1;proto=http',
   'X-Forwarded-For':'127.0.0.1','X-Real-IP':'127.0.0.1','X-Forwarded-Proto':'http'})
 observed=json.loads(result['forwarded']['body'])
 assert result['forwarded']['status']==200 and observed['revision']==a['revision'],result
 assert '127.0.0.1' not in (observed['forwarded_for'] or ''),observed
 assert observed['forwarded_proto']=='https',observed
 try:
  with socket.create_connection((a['address'],a['ports']['443']),timeout=5) as raw:
   context.wrap_socket(raw,server_hostname='unapproved.example.test')
 except ssl.SSLError:result['wrong-sni']='rejected'
 assert result.get('wrong-sni')=='rejected',result
elif a['mode']=='websocket':
 with socket.create_connection((a['address'],a['ports']['443']),timeout=5) as raw:
  with context.wrap_socket(raw,server_hostname=a['hostname']) as stream:
   key=base64.b64encode(os.urandom(16)).decode()
   stream.sendall(('GET /ws HTTP/1.1\r\nHost: '+a['hostname']+'\r\nUpgrade: websocket\r\n'
    'Connection: Upgrade\r\nSec-WebSocket-Version: 13\r\n'
    'Sec-WebSocket-Key: '+key+'\r\n\r\n').encode())
   response=b''
   while b'\r\n\r\n' not in response:
    response+=stream.recv(1);assert len(response)<8192
   assert response.startswith(b'HTTP/1.1 101'),response
   accepted=base64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest())
   assert accepted in response,response
   body=b'approved-websocket';mask=os.urandom(4)
   stream.sendall(bytes([129,128+len(body)])+mask+bytes(x^mask[i%4] for i,x in enumerate(body)))
   def read(size):
    data=b''
    while len(data)<size:
     chunk=stream.recv(size-len(data));assert chunk;data+=chunk
    return data
   header=read(2);assert header==bytes([129,len(body)]),header
   assert read(len(body))==body
   result={'status':101,'echo':body.decode(),'tls_verified':True}
elif a['mode']=='withdrawn':
 result=request('/health');assert result['status'] in (404,421),result
else:raise ValueError('unknown probe mode')
print(json.dumps(result))
"""


class IngressAcceptance:
    def __init__(self, network):
        self.network = network
        self.delivery, self.proof = network.delivery, network.proof

    def client(self, mode, implementation="python", environment="test"):
        other = "prod" if environment == "test" else "test"
        return external_client(
            self.proof,
            self.delivery,
            implementation,
            environment,
            CLIENT,
            {
                "mode": mode,
                "other_hostname": f"{implementation}.{other}.{self.delivery.prefix}.example.test",
            },
        )

    def negative(self):
        network = self.network
        original = network.current("python", "test")["desired"]
        evidence = {}
        try:
            configuration = copy.deepcopy(original)
            configuration["ingress"].update(path="/health", path_type="Exact")
            evidence["exact-route"] = network.apply(
                "python", "test", configuration, "exact-route-" + str(uuid4())
            )
            evidence["negative"] = self.client("negative")
            configuration["ingress"].update(
                path="/", path_type="PathPrefix", max_request_bytes=1024, timeout_seconds=1
            )
            evidence["limits-change"] = network.apply(
                "python", "test", configuration, "limits-" + str(uuid4())
            )
            evidence["limits"] = self.client("limits")
            configuration["ingress"].update(
                streaming=True, max_request_bytes=None, timeout_seconds=30
            )
            evidence["streaming-change"] = network.apply(
                "python", "test", configuration, "streaming-" + str(uuid4())
            )
            evidence["websocket"] = self.client("websocket")
        finally:
            evidence["restored"] = network.apply(
                "python", "test", original, "restore-ingress-" + str(uuid4())
            )
        return network.save("negative-ingress", evidence)

    def tls(self):
        network, delivery, proof = self.network, self.delivery, self.proof
        namespace = delivery.prefix + "-ingress"

        def secret():
            # Keep private key material out of the acceptance command transcript.
            return json.loads(
                subprocess.check_output(
                    [
                        "kubectl",
                        "--context",
                        proof.state["context"],
                        "-n",
                        namespace,
                        "get",
                        "secret",
                        "test-tls",
                        "-o",
                        "json",
                    ],
                    timeout=30,
                )
            )

        original = secret()
        original_data = original["data"]
        hosts = [f"{name}.test.{delivery.prefix}.example.test" for name in ("python", "node")]
        now = datetime.datetime.now(datetime.UTC)
        evidence = {}

        def replace(cert, key):
            current = secret()
            if current["metadata"]["uid"] != original["metadata"]["uid"]:
                raise RuntimeError("certificate identity changed during acceptance")
            current["data"] = {
                "tls.crt": base64.b64encode(cert).decode(),
                "tls.key": base64.b64encode(key).decode(),
            }
            proof.kube("replace", "-f", "-", document=current)

        try:
            for name, names, options in (
                (
                    "expired",
                    hosts,
                    {
                        "valid_from": now - datetime.timedelta(days=2),
                        "valid_until": now - datetime.timedelta(days=1),
                    },
                ),
                ("future", hosts, {"valid_from": now + datetime.timedelta(days=1)}),
                ("wrong-host", ["unapproved.example.test"], {}),
            ):
                cert, key = certificate(names, **options)
                replace(cert, key)
                result = network.probe("python", "test", name + "-certificate-" + str(uuid4()))
                if result["status"] != "failed":
                    raise RuntimeError("invalid certificate passed independent verification")
                evidence[name] = result
        finally:
            replace(
                base64.b64decode(original_data["tls.crt"]),
                base64.b64decode(original_data["tls.key"]),
            )
        old = network.current("python", "test")["observed"]["certificate"]["sha256"]
        cert, key = certificate(hosts)
        replace(cert, key)
        (delivery.output / "test-gateway-ca.pem").write_bytes(cert)
        for implementation in ("python", "node"):
            configuration = network.current(implementation, "test")["desired"]
            evidence["rotation-" + implementation] = network.apply(
                implementation, "test", configuration, "rotate-certificate-" + str(uuid4())
            )
            current = network.current(implementation, "test")["observed"]["certificate"]
            if current["sha256"] == old:
                raise RuntimeError("rotation retained the previous certificate fingerprint")
        from pipeline_network_fixture import external_ingress

        evidence["external"] = external_ingress(proof, delivery, "python", "test")
        evidence["secret_uid"] = original["metadata"]["uid"]
        return network.save("tls-lifecycle", evidence)
