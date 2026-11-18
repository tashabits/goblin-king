"""Fenced local SQLite backup and restore into a separate, recovery-only claim."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import tarfile
import tempfile
import time
from pathlib import Path, PurePosixPath
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]


def verify_artifact_backup(output, *, state_sha256=None):
    """Verify the exported image graph without extracting archive paths."""
    metadata = json.loads((output / "artifacts.json").read_text())
    if state_sha256 is not None and metadata.get("state_sha256") != state_sha256:
        raise RuntimeError("artifact backup belongs to a different state snapshot")
    path = output / "artifacts.tar"
    with path.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != metadata["sha256"]:
            raise RuntimeError("artifact backup checksum mismatch")
    with tarfile.open(path) as archive:
        members = {}
        total = 0
        for member in archive:
            relative = PurePosixPath(member.name)
            if (
                relative.is_absolute()
                or ".." in relative.parts
                or not relative.parts
                or relative.parts[0] != "artifacts"
                or member.name in members
                or not (member.isdir() or member.isfile())
            ):
                raise RuntimeError("artifact backup contains unsafe entries")
            total += member.size
            if member.size > 512 * 1024**2 or total > 2 * 1024**3 or len(members) >= 10000:
                raise RuntimeError("artifact backup exceeds supported size limits")
            members[member.name] = member

        def read(name, maximum=8 * 1024**2):
            member = members.get(name)
            if member is None or not member.isfile() or member.size > maximum:
                raise RuntimeError("artifact metadata is missing or oversized")
            return archive.extractfile(member).read()

        images = json.loads(read("artifacts/index.json"))["images"]
        for image in images:
            digest = image["digest"]
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
                raise RuntimeError("artifact manifest digest is invalid")
            raw = read("artifacts/blobs/sha256/" + digest.removeprefix("sha256:"))
            if "sha256:" + hashlib.sha256(raw).hexdigest() != digest:
                raise RuntimeError("artifact manifest checksum mismatch")
            manifest = json.loads(raw)
            for descriptor in [manifest["config"], *manifest["layers"]]:
                blob = descriptor["digest"]
                if (
                    not re.fullmatch(r"sha256:[0-9a-f]{64}", blob)
                    or descriptor.get("urls")
                    or not 0 <= descriptor["size"] <= 512 * 1024**2
                ):
                    raise RuntimeError("artifact descriptor is invalid or oversized")
                member = members.get("artifacts/blobs/sha256/" + blob.removeprefix("sha256:"))
                if member is None or not member.isfile() or member.size != descriptor["size"]:
                    raise RuntimeError("artifact blob is missing or has changed size")
                with archive.extractfile(member) as stream:
                    actual = "sha256:" + hashlib.file_digest(stream, "sha256").hexdigest()
                if actual != blob:
                    raise RuntimeError("artifact blob checksum mismatch")
        if len(images) != metadata["images"]:
            raise RuntimeError("artifact inventory count changed")
        return {"images": images, "sha256": metadata["sha256"]}


def restore_artifact_backup(output, clients, *, state_sha256):
    """Restore a checked graph to explicitly mapped repositories, preserving every digest."""
    inventory = verify_artifact_backup(output, state_sha256=state_sha256)
    for image in inventory["images"]:
        repository = image.get("repository", "")
        if repository not in clients or not re.fullmatch(
            r"[a-z0-9]+(?:[._/-][a-z0-9]+)*", repository
        ):
            raise RuntimeError("backup repository has no approved restoration destination")
        if not re.fullmatch(r"[0-9a-f-]{36}", image.get("build_id", "")):
            raise RuntimeError("backup publication identity is invalid")
    restored = []
    with tarfile.open(output / "artifacts.tar") as archive:
        for image in inventory["images"]:
            repository, digest = image["repository"], image["digest"]
            client = clients[repository]
            raw = archive.extractfile("artifacts/blobs/sha256/" + digest[7:]).read()
            manifest = json.loads(raw)
            for descriptor in [manifest["config"], *manifest["layers"]]:
                blob = descriptor["digest"]
                with archive.extractfile("artifacts/blobs/sha256/" + blob[7:]) as stream:
                    client.blob(repository, blob, stream.read())
            if client.manifest(repository, image["build_id"], raw) != digest:
                raise RuntimeError("restored publication digest changed")
            # Read back the bytes, including layers, independently of upload acknowledgement.
            from urllib.request import Request

            for descriptor in [manifest["config"], *manifest["layers"]]:
                blob = descriptor["digest"]
                request = Request(
                    client.endpoint + "/v2/" + repository + "/blobs/" + blob,
                    headers={"Authorization": client.authorization},
                )
                checksum, size = hashlib.sha256(), 0
                with client.opener.open(request, timeout=60) as stream:
                    while chunk := stream.read(1024 * 1024):
                        size += len(chunk)
                        if size > descriptor["size"]:
                            raise RuntimeError("restored blob exceeds its declared size")
                        checksum.update(chunk)
                if size != descriptor["size"] or checksum.hexdigest() != blob[7:]:
                    raise RuntimeError("restored blob checksum mismatch")
            restored.append(image | {"verified": True})
    result = {
        "backup_sha256": inventory["sha256"],
        "state_sha256": state_sha256,
        "images": restored,
    }
    (output / "artifact-restoration.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


class StorageRecovery:
    def __init__(self, context, namespace, release, output):
        self.context, self.namespace, self.release = context, namespace, release
        self.output = output.resolve()
        self.output.mkdir(parents=True, exist_ok=True)

    def command(self, args, document=None, timeout=120):
        result = subprocess.run(
            args,
            input=json.dumps(document) if document is not None else None,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
        )
        if result.returncode:
            raise RuntimeError(f"{args[0]} failed: {result.stderr[-1500:]}")
        return result.stdout

    def kube(self, *args, document=None, timeout=120):
        return self.command(
            ["kubectl", "--context", self.context, "-n", self.namespace, *args], document, timeout
        )

    def helm(self, *args):
        return self.command(
            ["helm", "--kube-context", self.context, "-n", self.namespace, *args], timeout=240
        )

    def values(self):
        return json.loads(self.helm("get", "values", self.release, "--all", "-o", "json"))

    def inventory(self):
        script = """
import json,os
from urllib.request import Request,urlopen
def get(path):
 request=Request('http://127.0.0.1:8000'+path,
     headers={'Authorization':'Bearer '+os.environ['GOBLIN_KING_BOOTSTRAP_ADMIN_TOKEN']})
 with urlopen(request,timeout=30) as response:return json.load(response)
state=get('/pipeline/recovery')
state['networking']={row['key']:get('/pipeline/releases/'+row['key']+'/networking')
                    for row in state['releases']}
print(json.dumps(state))
"""
        return json.loads(
            self.kube(
                "exec",
                "-i",
                "deploy/" + self.release + "-api",
                "--",
                "python",
                "-c",
                script,
            )
        )

    def check_fence(self, claim):
        deployments = json.loads(self.kube("get", "deployments", "-o", "json"))["items"]
        for deployment in deployments:
            volumes = deployment["spec"]["template"]["spec"].get("volumes", [])
            if deployment["spec"].get("replicas", 1) and any(
                volume.get("persistentVolumeClaim", {}).get("claimName") == claim
                for volume in volumes
            ):
                raise RuntimeError("a controller can still start a writer on the source claim")
        pods = json.loads(self.kube("get", "pods", "-o", "json"))["items"]
        for pod in pods:
            if pod["status"]["phase"] in {"Succeeded", "Failed"}:
                continue
            volumes = pod["spec"].get("volumes", [])
            if any(v.get("persistentVolumeClaim", {}).get("claimName") == claim for v in volumes):
                raise RuntimeError("source claim still has a live Pod; fencing is incomplete")
            if pod["metadata"].get("labels", {}).get("goblin-king.io/pipeline"):
                raise RuntimeError("delivery execution remains active; settle it before backup")
        jobs = json.loads(self.kube("get", "jobs", "-o", "json"))["items"]
        if any(
            not (job.get("status", {}).get("succeeded") or job.get("status", {}).get("failed"))
            for job in jobs
        ):
            raise RuntimeError("unsettled execution Jobs remain; delivery stays fenced")
        return [
            {
                "name": p["metadata"]["name"],
                "uid": p["metadata"]["uid"],
                "phase": p["status"]["phase"],
            }
            for p in pods
        ]

    def helper(self, claim, node, image, readonly, registry_secret=None):
        name = "pipeline-storage-" + str(uuid4())[:8]
        self.kube(
            "create",
            "-f",
            "-",
            document={
                "apiVersion": "v1",
                "kind": "Pod",
                "metadata": {"name": name, "labels": {"goblin-king.io/storage-recovery": "true"}},
                "spec": {
                    "nodeSelector": {"kubernetes.io/hostname": node},
                    "tolerations": [{"operator": "Exists"}],
                    "automountServiceAccountToken": False,
                    "restartPolicy": "Never",
                    "terminationGracePeriodSeconds": 1,
                    "securityContext": {"runAsUser": 65532, "runAsGroup": 65532, "fsGroup": 65532},
                    "containers": [
                        {
                            "name": "copy",
                            "image": image,
                            "imagePullPolicy": "IfNotPresent",
                            "command": ["python", "-c", "import time; time.sleep(900)"],
                            "resources": {
                                "requests": {"cpu": "50m", "memory": "128Mi"},
                                "limits": {"cpu": "1", "memory": "1Gi"},
                            },
                            "securityContext": {
                                "allowPrivilegeEscalation": False,
                                "readOnlyRootFilesystem": True,
                                "capabilities": {"drop": ["ALL"]},
                                "seccompProfile": {"type": "RuntimeDefault"},
                            },
                            "volumeMounts": [
                                {"name": "data", "mountPath": "/data", "readOnly": readonly},
                                {"name": "tmp", "mountPath": "/tmp"},
                            ]
                            + (
                                [{"name": "registry", "mountPath": "/registry", "readOnly": True}]
                                if registry_secret
                                else []
                            ),
                        }
                    ],
                    "volumes": [
                        {"name": "data", "persistentVolumeClaim": {"claimName": claim}},
                        {"name": "tmp", "emptyDir": {}},
                    ]
                    + (
                        [{"name": "registry", "secret": {"secretName": registry_secret}}]
                        if registry_secret
                        else []
                    ),
                },
            },
        )
        self.kube("wait", "--for=condition=Ready", "pod/" + name, "--timeout=90s")
        return name

    def remove_helper(self, name):
        self.kube("delete", "pod", name, "--wait=true", "--timeout=60s")

    def restore_artifacts(self, config_path):
        """Use Secret references and a host-reachable TLS endpoint for disaster restoration."""
        from goblin_king.pipeline_oci import RegistryClient

        config = json.loads(config_path.read_text())
        metadata = json.loads((self.output / "backup.json").read_text())
        if metadata["namespace"] != self.namespace or metadata["release"] != self.release:
            raise RuntimeError("backup belongs to another installation")
        # The source state remains fenced until actual releases have been reconciled.
        namespace = json.loads(self.kube("get", "namespace", self.namespace, "-o", "json"))
        claim = json.loads(self.kube("get", "pvc", metadata["source_claim"], "-o", "json"))
        if (
            namespace["metadata"]["uid"] != metadata["namespace_uid"]
            or claim["metadata"]["uid"] != metadata["source_claim_uid"]
        ):
            raise RuntimeError("backup source identity changed")
        self.check_fence(metadata["source_claim"])
        with tempfile.TemporaryDirectory(prefix="goblin-registry-restore-") as temporary:
            clients = {}
            for repository, reference in config["repositories"].items():
                raw = json.loads(
                    self.command(
                        [
                            "kubectl",
                            "--context",
                            self.context,
                            "-n",
                            reference["namespace"],
                            "get",
                            "secret",
                            reference["name"],
                            "-o",
                            "json",
                        ]
                    )
                )["data"]
                ca_path = Path(temporary) / (
                    hashlib.sha256(repository.encode()).hexdigest() + ".crt"
                )
                ca_path.write_bytes(base64.b64decode(raw["ca.crt"]))
                clients[repository] = RegistryClient(
                    config["endpoint"],
                    base64.b64decode(raw["username"]).decode(),
                    base64.b64decode(raw["password"]).decode(),
                    ca_path,
                )
            return restore_artifact_backup(self.output, clients, state_sha256=metadata["sha256"])

    def artifacts(self):
        """Back up verified images outside registry storage while delivery remains fenced."""
        metadata = json.loads((self.output / "backup.json").read_text())
        if metadata["namespace"] != self.namespace or metadata["release"] != self.release:
            raise RuntimeError("backup belongs to another installation")
        namespace = json.loads(self.kube("get", "namespace", self.namespace, "-o", "json"))
        pvc = json.loads(self.kube("get", "pvc", metadata["source_claim"], "-o", "json"))
        if (
            namespace["metadata"]["uid"] != metadata["namespace_uid"]
            or pvc["metadata"]["uid"] != metadata["source_claim_uid"]
        ):
            raise RuntimeError("backup source identity changed")
        self.check_fence(metadata["source_claim"])
        values = metadata["values"]
        helper = self.helper(
            metadata["source_claim"],
            metadata["source_node"],
            metadata["image"],
            True,
            values["pipeline"]["registryReaderSecret"],
        )
        script = r"""
import hashlib,json,sqlite3,tarfile
from pathlib import Path
from urllib.request import Request
from goblin_king.pipeline_oci import DIGEST, RegistryClient
db=sqlite3.connect('file:/data/' + DBNAME + '?mode=ro',uri=True)
client=RegistryClient(ENDPOINT,Path('/registry/username').read_text(),
    Path('/registry/password').read_text(),Path('/registry/ca.crt'))
root=Path('/tmp/artifacts'); root.mkdir()
blobs=root/'blobs'/'sha256'; blobs.mkdir(parents=True)
images={}
for (raw,) in db.execute('select document from pipeline_builds'):
    document=json.loads(raw)
    if not document.get('verification'): continue
    for name,proof in (document.get('result') or {}).get('data',{}).get('evidence',{}).items():
        if name.endswith('-publication'):
            repository,digest=proof['repository'],proof['digest']
            if not DIGEST.fullmatch(digest): raise RuntimeError('invalid image digest')
            images[repository+'@'+digest]={'repository':repository,'digest':digest,
                'build_id':proof['build_id']}
total=0
for image in images.values():
    repository,digest=image['repository'],image['digest']
    _,_,raw=client.request('/v2/'+repository+'/manifests/'+digest)
    if hashlib.sha256(raw).hexdigest()!=digest[7:]: raise RuntimeError('manifest mismatch')
    manifest=json.loads(raw); (blobs/digest[7:]).write_bytes(raw)
    for descriptor in [manifest['config'],*manifest['layers']]:
        digest,size=descriptor['digest'],descriptor['size']
        if not DIGEST.fullmatch(digest) or descriptor.get('urls') or not 0<=size<=512*1024**2:
            raise RuntimeError('invalid or oversized artifact descriptor')
        path=blobs/digest[7:]
        if path.exists(): continue
        total+=size
        if total>2*1024**3: raise RuntimeError('backup exceeds 2 GiB; delivery remains fenced')
        request=Request(client.endpoint+'/v2/'+repository+'/blobs/'+digest,
            headers={'Authorization':client.authorization})
        checksum=hashlib.sha256(); count=0
        with client.opener.open(request,timeout=60) as response,path.open('wb') as output:
            while chunk:=response.read(1024*1024):
                count+=len(chunk)
                if count>size: raise RuntimeError('artifact size overflow')
                checksum.update(chunk); output.write(chunk)
        if count!=size or checksum.hexdigest()!=digest[7:]:
            raise RuntimeError('artifact checksum mismatch')
(root/'index.json').write_text(json.dumps({'schema':1,'images':list(images.values())},indent=2))
with tarfile.open('/tmp/artifacts.tar','w') as archive: archive.add(root,arcname='artifacts')
print(json.dumps({'images':len(images),'blob_bytes':total}))
""".replace("DBNAME", repr(Path(values["config"]["dbPath"]).name)).replace(
            "ENDPOINT", repr(values["pipeline"]["settings"]["registry_endpoint"])
        )
        path = self.output / "artifacts.tar"
        temporary = path.with_suffix(".partial")
        try:
            summary = json.loads(
                self.kube("exec", helper, "--", "python", "-c", script, timeout=600)
            )
            with temporary.open("wb") as stream:
                result = subprocess.run(
                    [
                        "kubectl",
                        "--context",
                        self.context,
                        "-n",
                        self.namespace,
                        "exec",
                        helper,
                        "--",
                        "cat",
                        "/tmp/artifacts.tar",
                    ],
                    stdout=stream,
                    stderr=subprocess.PIPE,
                    timeout=600,
                )
            if result.returncode:
                raise RuntimeError("artifact transfer failed; partial backup retained")
            with temporary.open("rb") as stream:
                summary["sha256"] = hashlib.file_digest(stream, "sha256").hexdigest()
            summary["state_sha256"] = metadata["sha256"]
            temporary.replace(path)
            (self.output / "artifacts.json").write_text(json.dumps(summary, indent=2))
            return summary
        finally:
            self.remove_helper(helper)

    def backup(self):
        values = self.values()
        if (
            not values["pipeline"]["enabled"]
            or values["persistence"]["topology"] != "sqlite-single-node"
        ):
            raise RuntimeError("backup requires the supported single-node delivery installation")
        claim = values["persistence"].get("existingClaim") or self.release + "-data"
        namespace = json.loads(self.kube("get", "namespace", self.namespace, "-o", "json"))
        pvc = json.loads(self.kube("get", "pvc", claim, "-o", "json"))
        if (
            not values["persistence"].get("existingClaim")
            and pvc["metadata"].get("annotations", {}).get("helm.sh/resource-policy") != "keep"
        ):
            raise RuntimeError("upgrade the chart to retain the source claim before backup")
        # Scaling is persistent fencing; failures intentionally leave the writers stopped.
        for component in ("api", "scheduler", "directory-api"):
            name = self.release + "-" + component
            if self.kube("get", "deployment", name, "--ignore-not-found", "-o", "name").strip():
                self.kube("scale", "deployment/" + name, "--replicas=0")
        deadline = time.monotonic() + 120
        while True:
            try:
                fence = self.check_fence(claim)
                break
            except RuntimeError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(2)
        image = values["image"]["repository"] + "@" + values["image"]["digest"]
        if values["image"]["digest"].startswith("sha256:") is False:
            raise RuntimeError("backup helper requires a digest-pinned image")
        helper = self.helper(claim, values["persistence"]["sqliteNode"], image, True)
        script = """
import base64, io, json, sqlite3, tarfile
from pathlib import Path
root=Path('/data')
for path in root.rglob('*'):
    if path.is_symlink(): raise RuntimeError('backup refuses symbolic links')
db=sqlite3.connect('file:' + str(root / DBNAME) + '?mode=ro', uri=True)
assert db.execute('pragma integrity_check').fetchone()[0] == 'ok'
with tarfile.open('/tmp/state.tar.gz','w:gz') as archive: archive.add(root, arcname='state')
print(base64.b64encode(Path('/tmp/state.tar.gz').read_bytes()).decode())
""".replace("DBNAME", repr(Path(values["config"]["dbPath"]).name))
        try:
            raw = base64.b64decode(self.kube("exec", helper, "--", "python", "-c", script))
        finally:
            self.remove_helper(helper)
        archive = self.output / "state.tar.gz"
        archive.write_bytes(raw)
        metadata = {
            "schema": 1,
            "namespace": self.namespace,
            "namespace_uid": namespace["metadata"]["uid"],
            "release": self.release,
            "source_claim": claim,
            "source_claim_uid": pvc["metadata"]["uid"],
            "source_node": values["persistence"]["sqliteNode"],
            "image": image,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "fence": fence,
            "values": values,
        }
        (self.output / "backup.json").write_text(json.dumps(metadata, indent=2))
        # Execution identities are necessary evidence and contain no Secret resources.
        (self.output / "execution-evidence.json").write_text(
            self.kube("get", "jobs,configmaps", "-o", "json")
        )
        return {"backup": str(archive), "sha256": metadata["sha256"], "writers": "stopped"}

    def restore(self, node):
        metadata = json.loads((self.output / "backup.json").read_text())
        if metadata["namespace"] != self.namespace or metadata["release"] != self.release:
            raise RuntimeError("backup belongs to another installation")
        ns = json.loads(self.kube("get", "namespace", self.namespace, "-o", "json"))
        if ns["metadata"]["uid"] != metadata["namespace_uid"]:
            raise RuntimeError("namespace identity changed; explicit migration is required")
        pvc = json.loads(self.kube("get", "pvc", metadata["source_claim"], "-o", "json"))
        if pvc["metadata"]["uid"] != metadata["source_claim_uid"]:
            raise RuntimeError("source storage identity changed")
        self.check_fence(metadata["source_claim"])
        raw = (self.output / "state.tar.gz").read_bytes()
        if hashlib.sha256(raw).hexdigest() != metadata["sha256"]:
            raise RuntimeError("backup checksum mismatch")
        artifacts = verify_artifact_backup(self.output, state_sha256=metadata["sha256"])
        claim = self.release + "-restore-" + str(uuid4())[:8]
        values = metadata["values"]
        self.kube(
            "create",
            "-f",
            "-",
            document={
                "apiVersion": "v1",
                "kind": "PersistentVolumeClaim",
                "metadata": {"name": claim, "labels": {"goblin-king.io/storage-recovery": "true"}},
                "spec": {
                    "accessModes": ["ReadWriteOnce"],
                    "resources": {"requests": {"storage": values["persistence"]["size"]}},
                    **(
                        {"storageClassName": values["persistence"]["storageClassName"]}
                        if values["persistence"]["storageClassName"]
                        else {}
                    ),
                },
            },
        )
        helper = self.helper(claim, node, metadata["image"], False)
        script = """
import base64, io, json, tarfile, sys
from pathlib import Path, PurePosixPath
raw=base64.b64decode(json.load(sys.stdin)['archive'])
with tarfile.open(fileobj=io.BytesIO(raw), mode='r:gz') as archive:
    for member in archive:
        path=PurePosixPath(member.name)
        if path.is_absolute() or '..' in path.parts or path.parts[0] != 'state':
            raise RuntimeError('invalid backup path')
        target=Path('/data').joinpath(*path.parts[1:])
        if member.isdir(): target.mkdir(parents=True, exist_ok=True)
        elif member.isfile():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.extractfile(member).read())
        else: raise RuntimeError('backup contains unsupported file type')
print('restored')
"""
        try:
            self.kube(
                "exec",
                "-i",
                helper,
                "--",
                "python",
                "-c",
                script,
                document={"archive": base64.b64encode(raw).decode()},
            )
        finally:
            self.remove_helper(helper)
        values["persistence"]["existingClaim"] = claim
        values["persistence"]["sqliteNode"] = node
        values["pipeline"]["settings"]["recovery_mode"] = True
        values["scheduler"]["enabled"] = False
        values["api"]["replicas"] = 1
        values["api"]["nodeSelector"] = {"kubernetes.io/hostname": node}
        values["api"]["tolerations"] = [{"operator": "Exists"}]
        path = self.output / "restore-values.json"
        path.write_text(json.dumps(values, indent=2))
        self.helm(
            "upgrade",
            self.release,
            str(ROOT / "charts/goblin-king"),
            "-f",
            str(path),
            "--wait=watcher",
            "--timeout=180s",
        )
        if self.inventory()["recovery_mode"] is not True:
            raise RuntimeError("restored API did not enter recovery mode; keep scheduling disabled")
        return {
            "claim": claim,
            "node": node,
            "recovery_mode": True,
            "scheduler": "disabled",
            "verified_artifacts": len(artifacts["images"]),
            "next": "Reconcile releases and execution identities before enabling delivery.",
        }

    def reconcile(self, *, enable=False):
        values = self.values()
        if (
            not values["pipeline"]["settings"].get("recovery_mode")
            or values["scheduler"]["enabled"]
        ):
            raise RuntimeError("reconciliation requires recovery mode with scheduling disabled")
        state = self.inventory()
        if state["recovery_mode"] is not True:
            raise RuntimeError("live API is not held in recovery mode")
        if any(
            row["status"] not in {"completed", "failed", "superseded"}
            for row in state["operations"]
        ):
            raise RuntimeError(
                "interrupted operations require explicit recovery before enabling delivery"
            )
        if any(row["status"] in {"admitted", "held"} for row in state["builds"]):
            raise RuntimeError(
                "unsettled build identities require reconciliation before enabling delivery"
            )
        evidence = []
        for row in state["releases"]:
            doc = row["document"]
            observed = doc["observed"]
            if doc["operation_id"]:
                raise RuntimeError("release ownership remains unresolved")
            if not observed:
                network = state.get("networking", {}).get(row["key"])
                if network:
                    result = self.reconcile_network(values, row, network)
                    evidence.append({"release_key": row["key"], "networking": result})
                continue
            namespace, release = doc["environment"]["namespace"], doc["environment"]["release"]

            def application_helm(*args, namespace=namespace):
                result = subprocess.run(
                    [
                        "helm",
                        "--kube-context",
                        self.context,
                        "-n",
                        namespace,
                        *args,
                    ],
                    env=os.environ | {"HELM_DRIVER": "configmap"},
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=True,
                )
                return json.loads(result.stdout)

            history = application_helm("history", release, "-o", "json")
            actual = application_helm("get", "values", release, "-o", "json")
            if (
                history[-1]["status"] != "deployed"
                or history[-1]["revision"] != observed["helm_revision"]
                or actual["image"] != observed["image"]
                or actual["active"] != observed["active"]
            ):
                raise RuntimeError("actual Helm state differs from the restored observed release")
            resources = json.loads(
                self.kube(
                    "-n",
                    namespace,
                    "get",
                    "deployments,cronjobs,pods",
                    "-l",
                    "app.kubernetes.io/instance=" + release,
                    "-o",
                    "json",
                )
            )["items"]
            for resource in resources:
                if resource["kind"] == "Deployment":
                    expected = int(observed["active"])
                    if (
                        resource["spec"]["replicas"] != expected
                        or resource.get("status", {}).get("availableReplicas", 0) != expected
                    ):
                        raise RuntimeError("restored activation differs from actual workloads")
                if (
                    resource["kind"] == "CronJob"
                    and resource["spec"]["suspend"] == observed["active"]
                ):
                    raise RuntimeError("actual schedule activation differs from restored state")
                if resource["kind"] == "Pod" and not observed["active"]:
                    raise RuntimeError("inactive release still has application activity")
            health = None
            if observed["active"]:
                url = (
                    f"http://{release}.{namespace}.svc:{observed['port']}{observed['health_path']}"
                )
                health = self.kube(
                    "exec",
                    "-i",
                    "deploy/" + self.release + "-api",
                    "--",
                    "python",
                    "-c",
                    "import sys,json; from urllib.request import urlopen; "
                    "response=urlopen(json.load(sys.stdin)['url'],timeout=10); "
                    "assert response.status==200; print(response.read(8192).decode())",
                    document={"url": url},
                )
            network = state.get("networking", {}).get(row["key"])
            networking = self.reconcile_network(values, row, network) if network else None
            evidence.append(
                {
                    "release_key": row["key"],
                    "helm": history[-1],
                    "health": health,
                    "networking": networking,
                }
            )
        (self.output / "reconciliation.json").write_text(json.dumps(evidence, indent=2))
        if enable:
            values["pipeline"]["settings"]["recovery_mode"] = False
            values["scheduler"]["enabled"] = True
            values["scheduler"]["nodeSelector"] = {
                "kubernetes.io/hostname": values["persistence"]["sqliteNode"]
            }
            values["scheduler"]["tolerations"] = [{"operator": "Exists"}]
            path = self.output / "reconciled-values.json"
            path.write_text(json.dumps(values, indent=2))
            self.helm(
                "upgrade",
                self.release,
                str(ROOT / "charts/goblin-king"),
                "-f",
                str(path),
                "--wait=watcher",
                "--timeout=180s",
            )
            if self.inventory()["recovery_mode"] is not False:
                raise RuntimeError("live API still holds delivery in recovery mode")
        return {"reconciled_releases": len(evidence), "delivery_enabled": enable}

    def reconcile_network(self, values, release, network):
        if not network["network_version"]:
            return None
        from goblin_king.pipeline_kubernetes import restricted_job

        name = "network-restore-" + uuid4().hex[:12]
        target = release["document"]["environment"]
        payload = {
            "policy": values["pipeline"]["settings"]["networking"],
            "release": release,
            "network": network,
        }
        script = (
            "import json; from kubernetes import config; config.load_incluster_config(); "
            "from goblin_king.pipeline_network_recovery import verify_network_recovery; "
            "print(json.dumps(verify_network_recovery(**json.loads("
            + repr(json.dumps(payload))
            + "))))"
        )
        job = restricted_job(
            name,
            target["namespace"],
            values["pipeline"]["settings"]["network_image"],
            ["python", "-c", script],
            timeout=90,
        )
        template = job["spec"]["template"]
        template["metadata"].setdefault("labels", {})["goblin-king.io/trust"] = "network-control"
        template["spec"]["serviceAccountName"] = target["release"] + "-network"
        template["spec"]["automountServiceAccountToken"] = True
        self.kube("-n", target["namespace"], "create", "-f", "-", document=job)
        self.kube(
            "-n",
            target["namespace"],
            "wait",
            "--for=condition=Complete",
            "job/" + name,
            "--timeout=120s",
            timeout=135,
        )
        result = json.loads(self.kube("-n", target["namespace"], "logs", "job/" + name))
        return {"job": name, "namespace": target["namespace"], "observation": result}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=("backup", "artifacts", "restore-artifacts", "restore", "reconcile")
    )
    parser.add_argument("--context", default="docker-desktop")
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--release", default="delivery")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--node")
    parser.add_argument("--enable", action="store_true")
    parser.add_argument("--registry-config", type=Path)
    args = parser.parse_args()
    recovery = StorageRecovery(args.context, args.namespace, args.release, args.output)
    if args.command == "restore" and not args.node:
        parser.error("restore requires --node")
    if args.command == "restore-artifacts":
        if not args.registry_config:
            parser.error("restore-artifacts requires --registry-config")
        print(json.dumps(recovery.restore_artifacts(args.registry_config), indent=2))
        return
    result = (
        recovery.backup()
        if args.command == "backup"
        else recovery.artifacts()
        if args.command == "artifacts"
        else recovery.restore(args.node)
        if args.command == "restore"
        else recovery.reconcile(enable=args.enable)
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
