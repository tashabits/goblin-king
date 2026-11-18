"""Exercise authentication, project scope and racing configuration requests through HTTP."""

import copy
import json
from uuid import uuid4

AUTHORIZATION = r"""
import json,os,sys,uuid
from urllib.request import Request,urlopen
from urllib.error import HTTPError
a=json.load(sys.stdin);admin=os.environ['GOBLIN_KING_BOOTSTRAP_ADMIN_TOKEN']
def call(path,body=None,token=admin):
 headers={'Content-Type':'application/json'}
 if token:headers['Authorization']='Bearer '+token
 request=Request('http://127.0.0.1:8000'+path,
  data=json.dumps(body).encode() if body is not None else None,headers=headers)
 try:
  with urlopen(request,timeout=15) as response:return response.status,json.load(response)
 except HTTPError as error:return error.code,json.load(error)
_,person=call('/admin/users',{'email':'network-'+str(uuid.uuid4())+'@localhost',
 'display_name':'Networking acceptance'})
result={}
for role,project in [('viewer',a['project']),('member','another-project')]:
 status,issued=call('/admin/tokens',{'user_id':person['id'],'name':'network-boundary',
  'project_id':project,'role':role})
 assert status==200
 token=issued['raw_token']
 code,_=call('/pipeline/networking/preview',a['preview'],token)
 assert code==403,(role,code)
 if role=='member':
  code,_=call('/pipeline/releases/'+a['preview']['release_key']+'/networking',token=token)
  assert code==403,code
 result[role]={'status':403,'token_id':issued['token']['id']}
code,_=call('/pipeline/networking/preview',a['preview'],token=None)
assert code==401,code
result['unauthenticated']={'status':code}
print(json.dumps(result))
"""


def authorization(network):
    delivery, proof = network.delivery, network.proof
    release = delivery.release("python", "test")
    original = network.current("python", "test")["desired"]
    preview_body = {"release_key": release["key"], "configuration": original}
    operations = {row["id"] for row in delivery.collection("operations")}
    evidence = {
        "authentication": json.loads(
            proof.kube(
                "-n",
                delivery.control,
                "exec",
                "-i",
                "deploy/delivery-api",
                "--",
                "python",
                "-c",
                AUTHORIZATION,
                document={"project": delivery.project, "preview": preview_body},
            )
        )
    }
    invalid = {}
    for name, field, changes in (
        ("foreign-gateway", "ingress", {"gateway": "prod"}),
        ("unapproved-host", "ingress", {"hostname": "unapproved.example.test"}),
        ("secret-reference", "ingress", {"certificate": "foreign-secret"}),
        ("backend-override", "ingress", {"backend_namespace": "kube-system"}),
        (
            "unapproved-egress",
            "egress",
            {
                "hosts": [
                    {"hostname": "denied.fixture.example.test", "protocol": "HTTP", "port": 8080}
                ]
            },
        ),
        ("protected-direct", "egress", {"direct": [{"cidr": "169.254.169.254/32", "port": 80}]}),
    ):
        configuration = copy.deepcopy(original)
        configuration[field].update(changes)
        invalid[name] = delivery.request(
            "/pipeline/networking/preview",
            preview_body | {"configuration": configuration},
            expected=(422,),
        )
    conflict = copy.deepcopy(original)
    conflict["ingress"]["hostname"] = network.current("node", "test")["desired"]["ingress"][
        "hostname"
    ]
    invalid["reserved-route"] = delivery.request(
        "/pipeline/networking/preview", preview_body | {"configuration": conflict}, expected=(409,)
    )
    preview = delivery.request("/pipeline/networking/preview", preview_body)
    endpoint = "/pipeline/releases/" + release["key"] + "/networking"
    invalid["stale-version"] = delivery.request(
        endpoint,
        {
            "configuration": original,
            "preview_id": preview["preview_id"],
            "expected_version": preview["expected_version"] - 1,
            "idempotency_key": "stale-" + str(uuid4()),
        },
        expected=(409,),
    )
    if operations != {row["id"] for row in delivery.collection("operations")}:
        raise RuntimeError("rejected configuration created executable work")
    identity = "repeat-network-" + str(uuid4())
    body = {
        "configuration": original,
        "preview_id": preview["preview_id"],
        "expected_version": preview["expected_version"],
        "idempotency_key": identity,
    }
    accepted = delivery.request(endpoint, body)
    repeated = delivery.request(endpoint, body)
    if repeated != accepted:
        raise RuntimeError("idempotent network request created another operation")
    conflict = copy.deepcopy(body)
    conflict["configuration"]["ingress"]["timeout_seconds"] += 1
    invalid["idempotency-conflict"] = delivery.request(endpoint, conflict, expected=(409,))
    settled = delivery.operation(accepted["operation_id"])
    if settled["status"] != "completed":
        raise RuntimeError("accepted idempotent request did not complete")
    evidence.update(rejected=invalid, accepted=accepted, repeated=repeated, operation=settled)
    return network.save("network-authorization", evidence)
