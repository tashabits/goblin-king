"""Prove HTTPS notification recovery against the two installed acceptance systems."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import copy
import hashlib
import json
from pathlib import Path
import secrets
import subprocess
import sys
import time
from uuid import uuid4

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from pipeline_acceptance import Acceptance
from pipeline_delivery_acceptance import DeliveryAcceptance

REVISION='437d2ca3debcdc6438dd15568fc62f4d2cc69ba6'
NAME='pipeline-notification-proof'
ADMIN_SCRIPT="""
import json, ssl, sys
from pathlib import Path
from urllib.request import Request,urlopen
args=json.load(sys.stdin)
body=args.get('body')
request=Request('https://localhost:8443/admin/'+args['action'],
 data=json.dumps(body).encode() if body is not None else None,
 headers={'Authorization':'Bearer '+Path('/tls/admin-token').read_text().strip(),'Content-Type':'application/json'})
with urlopen(request,context=ssl.create_default_context(cafile='/tls/cert.pem'),timeout=5) as response:
 print(response.read().decode())
"""
DB_SCRIPT="""
import json,sqlite3,sys
args=json.load(sys.stdin)
db=sqlite3.connect('file:/data/goblin-king.sqlite3?mode=ro',uri=True)
db.row_factory=sqlite3.Row
cursor=db.execute('SELECT * FROM pipeline_notifications WHERE id=?',(args['id'],)) if args.get('id') else db.execute('SELECT * FROM pipeline_notifications')
rows=[dict(row) for row in cursor]
for row in rows:
 row['document']=json.loads(row['document'])
 row['admin_event_count']=db.execute('SELECT count(*) FROM events WHERE id=?',(row['id'],)).fetchone()[0]
print(json.dumps(rows))
"""

def wait(description, predicate, timeout=180, interval=.6):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        value=predicate()
        if value: return value
        time.sleep(interval)
    raise RuntimeError('timed out waiting for '+description)

def run(suffix):
    proof=Acceptance(ROOT/f'.runlogs/pipeline-437d2ca3-{suffix}')
    assert proof.state['revision']==REVISION
    assert proof.state['scenarios']['side-effect-faults']['status']=='passed'
    assert not any(row['status']=='running' for row in proof.state['scenarios'].values())
    delivery=DeliveryAcceptance(proof)
    directory=delivery.output/'http-notifications'
    directory.mkdir(exist_ok=True)
    result_path=directory/'verification.json'
    if result_path.exists() and json.loads(result_path.read_text()).get('passed'):
        print(json.dumps({'run':suffix,'notifications':'already verified'}),flush=True)
        return
    ns=delivery.control
    def kube(*args,document=None): return proof.kube('-n',ns,*args,document=document)
    def api(script,document=None):
        return kube('exec','-i','deploy/delivery-api','--','python','-c',script,document=document)
    def db(identity=None): return json.loads(api(DB_SCRIPT,{'id':identity}))
    def admin(action='state',body=None):
        return json.loads(kube('exec','-i','pod/'+NAME,'--','python','-c',ADMIN_SCRIPT,document={'action':action,'body':body}))
    def control(**body): return admin('control',body)
    def pods():
        return json.loads(kube('get','pods','-l','app.kubernetes.io/instance=delivery,app.kubernetes.io/component=api','-o','json'))['items']
    def pod_identity():
        rows=[row for row in pods() if not row['metadata'].get('deletionTimestamp') and row['status']['phase']=='Running']
        assert len(rows)==1
        row=rows[0]; status=row['status']['containerStatuses'][0]
        return {'name':row['metadata']['name'],'uid':row['metadata']['uid'],'node':row['spec']['nodeName'],
          'container_id':status.get('containerID'),'restarts':status['restartCount'],'ready':status['ready']}
    def signal(number):
        target=pod_identity()
        assert target['node'] in ('desktop-control-plane','desktop-worker')
        identity=target['container_id'].removeprefix('containerd://')
        assert len(identity)==64 and all(c in '0123456789abcdef' for c in identity)
        inspected=json.loads(proof.command(['docker','exec',target['node'],'crictl','inspect',identity]))
        assert inspected['status']['labels']['io.kubernetes.pod.uid']==target['uid']
        assert inspected['status']['metadata']['name']=='api'
        # Signal from the node's ancestor PID namespace: namespace init ignores
        # some signals sent from inside its own container.
        proof.command(['docker','exec',target['node'],'ctr','-n','k8s.io','tasks','kill',
          '--signal',{9:'SIGKILL',19:'SIGSTOP'}[number],identity])
        if number==19:
            stopped=kube('exec',target['name'],'--','python','-c',
              "from pathlib import Path; print(next(line for line in Path('/proc/1/status').read_text().splitlines() if line.startswith('State:')))")
            assert 'T (stopped)' in stopped
        return target
    def restarted(previous):
        def ready():
            try: current=pod_identity()
            except (AssertionError,KeyError): return False
            assert current['uid']==previous['uid']
            return current if current['restarts']>previous['restarts'] and current['ready'] and current['container_id']!=previous['container_id'] else False
        return wait('actual API process replacement',ready,180,1)
    def snapshot():
        jobs=json.loads(proof.kube('get','jobs','-A','-o','json'))['items']
        return {'builds':sorted(row['id'] for row in delivery.collection('builds')),
          'jobs':sorted(row['metadata']['uid'] for row in jobs if row['metadata']['namespace']==ns),
          'releases':{row['key']:row['document']['observed'] for row in delivery.collection('releases')}}
    def emit():
        before={row['id'] for row in db()}
        current=next(row for row in delivery.collection('repositories') if row['key']==repository['key'])
        delivery.request('/pipeline/repositories/'+current['key']+'/management',
          {'enabled':not bool(current['enabled']),'expected_version':current['version'],'idempotency_key':str(uuid4())})
        rows=[row for row in db() if row['id'] not in before and row['document']['action']=='repository.management']
        assert len(rows)==1
        return rows[0]['id']
    def delivered(identity):
        row=db(identity)[0]
        return row if row['status']=='delivered' else False
    def held_attempt(identity):
        state=admin()
        attempts=[row for row in state['attempts'] if row['id']==identity and row['waiting']]
        return attempts[-1] if attempts else False
    def verify(identity):
        row=wait('durable notification acknowledgement',lambda:delivered(identity))
        state=admin(); attempts=[item for item in state['attempts'] if item['id']==identity]
        effect=state['effects'][identity]
        assert effect['commits']==1 and row['admin_event_count']==1
        assert attempts and all(item['authenticated'] and item['checksum']==effect['checksum'] for item in attempts)
        return {'id':identity,'notification':row,'attempts':attempts,'effect':effect}
    def helm(values):
        proof.command(['helm','upgrade','delivery',str(ROOT/'charts/goblin-king'),'--kube-context',proof.state['context'],
          '-n',ns,'-f',str(values),'--wait=watcher','--timeout','180s'],timeout=240)
    labels={'goblin-king.io/acceptance':proof.state['run_id'],'app':NAME}
    def resource(kind,spec,name=NAME,api_version='v1'):
        return {'apiVersion':api_version,'kind':kind,'metadata':{'name':name,'namespace':ns,'labels':labels},**spec}
    original_path=directory/'original-values.private.json'
    if not original_path.exists():
        original_path.write_text(proof.command(['helm','get','values','delivery','--kube-context',proof.state['context'],'-n',ns,'-o','json']))
    original=json.loads(original_path.read_text())
    assert not original['pipeline'].get('notificationSecret') and not original['pipeline']['settings'].get('notification_url')
    bundle=api("from pathlib import Path; print(Path('/etc/ssl/certs/ca-certificates.crt').read_text())")
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    subject=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,NAME)])
    now=datetime.now(timezone.utc)
    certificate=(x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key())
      .serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(minutes=5)).not_valid_after(now+timedelta(days=2))
      .add_extension(x509.BasicConstraints(ca=True,path_length=None),critical=True)
      .add_extension(x509.SubjectAlternativeName([x509.DNSName(value) for value in ('localhost',NAME,NAME+'.'+ns+'.svc',NAME+'.'+ns+'.svc.cluster.local')]),critical=False)
      .sign(key,hashes.SHA256()).public_bytes(serialization.Encoding.PEM).decode())
    token=secrets.token_urlsafe(32)
    tls=resource('Secret',{'type':'Opaque','stringData':{'token':token,'admin-token':secrets.token_urlsafe(32),'cert.pem':certificate,
      'key.pem':key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()).decode()}})
    client=resource('Secret',{'type':'Opaque','stringData':{'token':token,'ca-bundle.pem':bundle+'\n'+certificate}},NAME+'-client')
    (directory/'secrets.private.json').write_text(json.dumps([tls,client]))
    image=original['image']['repository']+'@'+original['image']['digest']
    deployment=json.loads(kube('get','deployment','delivery-api','-o','json'))
    api_selector=deployment['spec']['selector']['matchLabels']
    resources=[tls,client,
      resource('ConfigMap',{'data':{'server.py':(ROOT/'.runlogs/notification_sink_437d2ca3.py').read_text()}}),
      resource('Service',{'spec':{'selector':{'app':NAME},'ports':[{'port':8443,'targetPort':8443}]}}),
      resource('NetworkPolicy',{'spec':{'podSelector':{'matchLabels':{'app':NAME}},'policyTypes':['Ingress','Egress'],
        'ingress':[{'from':[{'podSelector':{'matchLabels':api_selector}}],'ports':[{'protocol':'TCP','port':8443}]}],'egress':[]}},api_version='networking.k8s.io/v1'),
      resource('NetworkPolicy',{'spec':{'podSelector':{'matchLabels':api_selector},'policyTypes':['Egress'],
        'egress':[{'to':[{'podSelector':{'matchLabels':{'app':NAME}}}],'ports':[{'protocol':'TCP','port':8443}]}]}},NAME+'-api',api_version='networking.k8s.io/v1'),
      resource('Pod',{'spec':{'automountServiceAccountToken':False,'restartPolicy':'Never',
        'securityContext':{'runAsNonRoot':True,'runAsUser':1000,'runAsGroup':1000,'fsGroup':1000,'seccompProfile':{'type':'RuntimeDefault'}},
        'containers':[{'name':'sink','image':image,'imagePullPolicy':'IfNotPresent','command':['python','-B','/app-proof/server.py'],
          'securityContext':{'allowPrivilegeEscalation':False,'readOnlyRootFilesystem':True,'capabilities':{'drop':['ALL']}},
          'resources':{'requests':{'cpu':'25m','memory':'32Mi'},'limits':{'cpu':'250m','memory':'128Mi'}},
          'readinessProbe':{'tcpSocket':{'port':8443},'periodSeconds':2},
          'volumeMounts':[{'name':'tls','mountPath':'/tls','readOnly':True},{'name':'script','mountPath':'/app-proof','readOnly':True},{'name':'data','mountPath':'/data'}]}],
        'volumes':[{'name':'tls','secret':{'secretName':NAME}},{'name':'script','configMap':{'name':NAME}},{'name':'data','emptyDir':{}}]}})]
    # Creation fails on unexpected existing resources; no replacement of someone else's fixture.
    kube('create','-f','-',document={'apiVersion':'v1','kind':'List','items':resources})
    kube('wait','pod/'+NAME,'--for=condition=Ready','--timeout=120s')
    values=copy.deepcopy(original)
    values['pipeline']['notificationSecret']=NAME+'-client'
    values['pipeline']['settings']['notification_url']='https://'+NAME+'.'+ns+'.svc:8443/events'
    values.setdefault('project',{}).setdefault('extraVolumes',[]).append({'name':'notification-proof-ca','secret':{'secretName':NAME+'-client'}})
    values['project'].setdefault('extraVolumeMounts',[]).append({'name':'notification-proof-ca','mountPath':'/etc/ssl/certs/ca-certificates.crt','subPath':'ca-bundle.pem','readOnly':True})
    configured=directory/'configured-values.private.json'
    configured.write_text(json.dumps(values))
    result={'revision':REVISION,'run_id':proof.state['run_id'],'namespace':ns,'image':image,'cases':[],'passed':False}
    def save(): result_path.write_text(json.dumps(result,indent=2))
    paused=False
    try:
        helm(configured)
        wait('initial notification drain',lambda:all(row['status']=='delivered' for row in db()))
        repository=next(row for row in delivery.collection('repositories') if row['document']['registration']['service_id']=='python')
        original_enabled=bool(repository['enabled'])
        before=snapshot()
        wrong=api("""from urllib.request import Request,urlopen
from urllib.error import HTTPError
try:
 urlopen(Request('"""+values['pipeline']['settings']['notification_url']+"""',data=b'{}',headers={'Authorization':'Bearer invalid'}),timeout=10)
except HTTPError as error: print(error.code)
""")
        assert wrong.strip()=='401'
        result['unauthenticated_status']=401
        for boundary in ('outage','before_effect','after_effect','after_response_before_ack','after_durable_ack'):
            mode={'outage':'unavailable','before_effect':'hold_before_effect','after_effect':'hold_after_effect','after_response_before_ack':'hold_after_effect','after_durable_ack':'normal'}[boundary]
            control(mode=mode)
            identity=emit()
            case={'boundary':boundary,'id':identity}
            if boundary=='outage':
                pending=wait('failed delivery retry',lambda:(lambda row:row if row['attempts']>=1 else False)(db(identity)[0]))
                assert pending['status']=='pending' and pending['not_before']>0 and pending['admin_event_count']==1
                assert identity not in admin()['effects']
                case['outage']=pending
                control(mode='normal')
            elif boundary in ('before_effect','after_effect','after_response_before_ack'):
                case['held']=wait('sink boundary',lambda:held_attempt(identity))
                state=admin()
                assert (identity in state['effects'])==(boundary!='before_effect')
                assert db(identity)[0]['status']=='pending'
                if boundary=='after_response_before_ack':
                    previous=signal(19); paused=True
                    control(mode='normal',release=identity,response=202)
                    case['response_sent']=wait('response while API stopped',lambda:any(row['id']==identity and row['response_sent'] for row in admin()['attempts']))
                    case['unacknowledged']=db(identity)[0]
                    assert case['unacknowledged']['status']=='pending'
                    signal(9); paused=False
                else:
                    previous=signal(9)
                    control(mode='normal',release=identity,response=503)
                case['before_restart']=previous
                case['after_restart']=restarted(previous)
            evidence=verify(identity)
            if boundary=='after_durable_ack':
                previous=signal(9)
                case['before_restart']=previous
                case['after_restart']=restarted(previous)
                time.sleep(max(12,original['pipeline']['settings']['reconcile_seconds']*2))
                following=verify(identity)
                assert len(following['attempts'])==len(evidence['attempts'])==1
                evidence=following
            elif boundary!='outage': assert len(evidence['attempts'])>=2
            case['evidence']=evidence
            result['cases'].append(case); save()
            print(json.dumps({'run':suffix,'http_notification':boundary,'status':'passed'}),flush=True)
        current=next(row for row in delivery.collection('repositories') if row['key']==repository['key'])
        if bool(current['enabled'])!=original_enabled:
            verify(emit())
        assert snapshot()==before
        result['no_build_or_deployment_side_effects']=True
        result['sink_state']=admin()
        result['resources']=[{'kind':item['kind'],'name':item['metadata']['name'],
          'uid':json.loads(kube('get',item['kind'],item['metadata']['name'],'-o','json'))['metadata']['uid']} for item in resources]
    finally:
        if paused: signal(9)
        control(mode='normal')
        helm(original_path)
        result['original_configuration_restored']=True
        save()
    result['passed']=True
    save()
    print(json.dumps({'run':suffix,'notification_proof':'passed','cases':len(result['cases'])}),flush=True)

if __name__=='__main__':
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(run,('one','two')))
