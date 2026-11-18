"""Application-side connection probes with positive controls and exact endpoint bounds."""

EGRESS = r"""
import json,sys,socket,ssl,urllib.request,urllib.error
a=json.load(sys.stdin)
proxy=urllib.request.build_opener(
    urllib.request.ProxyHandler({'http':'http://app-egress:3128','https':'http://app-egress:3128'}),
    urllib.request.HTTPSHandler(context=ssl.create_default_context(cadata=a['ca'])))
observed={}
for name,url in [
 ('http','http://allowed.fixture.example.test:8080/allowed'),
 ('https','https://allowed.fixture.example.test:8443/allowed'),
 ('denied-host','http://denied.fixture.example.test:8080/forbidden'),
 ('ip-literal','http://'+a['address']+':8080/forbidden'),
 ('metadata','http://169.254.169.254/forbidden'),
 ('connect-port','https://allowed.fixture.example.test:9002/forbidden'),
 ('redirect','http://allowed.fixture.example.test:8080/redirect'),
]:
 try:
  with proxy.open(url,timeout=15) as r:
   body=r.read(8192).decode(); observed[name]={'status':r.status,'body':body}
 except urllib.error.HTTPError as error: observed[name]={'status':error.code}
 except urllib.error.URLError as error: observed[name]={'error':str(error.reason)}
assert observed['http']['status']==observed['https']['status']==200,observed
assert json.loads(observed['http']['body'])['fixture']==a['identity'],observed
assert json.loads(observed['https']['body'])['fixture']==a['identity'],observed
for name in ('denied-host','ip-literal','metadata','redirect'):
 assert observed[name]['status']==403,observed
assert '403' in observed['connect-port'].get('error',''),observed
for name,host,port in [('direct-bypass',a['address'],8080),
 ('node-api',a['node_address'],6443),('control-redis','delivery-redis.'+a['control']+'.svc',6379),
 ('other-proxy','app-egress.'+a['other_proxy']+'.svc',3128),
 ('other-environment','app.'+a['other_namespace']+'.svc',8080)]:
 addresses=socket.getaddrinfo(host,port,type=socket.SOCK_STREAM)
 assert addresses,(name,'target must resolve before denial can be evidence')
 try:
  s=socket.create_connection((host,port),timeout=3);s.close();observed[name]='allowed'
 except OSError: observed[name]='denied'
 assert observed[name]=='denied',observed
with socket.create_connection((a['address'],9002),timeout=5) as s:
 s.sendall(b'approved-tcp'); assert s.recv(100)==b'approved-tcp'
observed['direct-tcp']='allowed'
with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as s:
 s.settimeout(5);s.sendto(b'approved-udp',(a['address'],9003));assert s.recv(100)==b'approved-udp'
observed['direct-udp']='allowed'
print(json.dumps(observed))
"""

DNS = r"""
import json,urllib.request,urllib.error
client=urllib.request.build_opener(urllib.request.ProxyHandler({'http':'http://app-egress:3128'}))
result={}
for name in ('allowed','rebind','mixed'):
 try:
  with client.open('http://'+name+'.fixture.example.test:8080/dns-proof',timeout=10) as response:
   result[name]=response.status
 except urllib.error.HTTPError as error:result[name]=error.code
 except urllib.error.URLError:result[name]='connection-denied'
print(json.dumps(result))
"""

NODE_TLS = r"""
import fs from 'node:fs';
const args=JSON.parse(fs.readFileSync(0,'utf8'));
const app=await import('file:///app/app.mjs');
const result=await app.outboundTLS(args.ca);
if(result.status!==200||!result.tls_verified||result.body.fixture!==args.identity)
  throw new Error('controlled TLS upstream did not verify');
console.log(JSON.stringify(result));
"""
