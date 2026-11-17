"""Interrupt real local executions at named effect boundaries and retain identities."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from uuid import NAMESPACE_URL, uuid4, uuid5

RELEASE_POINTS = (
    "dispatch.before_submit",
    "dispatch.after_submit",
    "dispatch.after_ack",
    "kubernetes.after_create",
    "kubernetes.after_identity_ack",
    "kubernetes.before_result_ack",
    "kubernetes.after_result_ack",
    "execution.before_result_ack",
    "execution.after_result_ack",
    "helm.before_upgrade",
    "helm.after_upgrade",
    "git.before_trees",
    "git.after_trees",
    "git.before_commits",
    "git.after_commits",
    "git.before_refs",
    "git.after_refs",
    "recording.after_ack",
    "acknowledgement.before_external",
    "acknowledgement.after_external",
)
BUILD_POINTS = (
    "sandbox.before_delete",
    "sandbox.after_delete",
    "sandbox.before_absence_ack",
    "sandbox.after_absence_ack",
    "registry.before_upload",
    "registry.after_upload",
    "registry.before_blob",
    "registry.after_blob",
    "registry.before_manifest",
    "registry.after_manifest",
)
ROLLBACK_POINTS = ("helm.before_rollback", "helm.after_rollback")
POINTS = (
    *RELEASE_POINTS,
    *BUILD_POINTS,
    *ROLLBACK_POINTS,
    "kubernetes.before_create",
)


class FaultAcceptance:
    def __init__(self, delivery):
        self.delivery, self.proof = delivery, delivery.proof
        self.directory = delivery.output / "faults"
        self.directory.mkdir(parents=True, exist_ok=True)

    def save(self, point, record):
        path = self.directory / (point + ".json")
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)

    def gate(self, plan):
        self.proof.apply(
            {
                "apiVersion": "v1",
                "kind": "ConfigMap",
                "metadata": {
                    "name": "pipeline-acceptance-faults",
                    "namespace": self.delivery.control,
                    "labels": {"goblin-king.io/acceptance": self.proof.state["run_id"]},
                },
                "data": {"fault.json": json.dumps(plan)},
            }
        )

    def history(self):
        return json.loads(
            self.proof.command(
                [
                    "helm",
                    "--kube-context",
                    self.proof.state["context"],
                    "history",
                    "app",
                    "-n",
                    self.delivery.prefix + "-python-test",
                    "-o",
                    "json",
                ],
                environment={"HELM_DRIVER": "configmap"},
            )
        )

    def wait_for_acknowledgements(self):
        def acknowledged():
            rows = self.delivery.collection("builds") + self.delivery.collection("operations")
            return all(
                row["document"].get("execution_acknowledged")
                for row in rows
                if row["status"] in {"published", "superseded", "completed", "failed"}
                and row["document"].get("job_id")
                and row["document"].get("result")
            )

        self.delivery.wait("prior execution acknowledgements", acknowledged, timeout=180)

    def bind_hit(self, record):
        """Reject an unrelated pause before terminating any process."""
        point = record["point"]
        marker = record["hit"]["marker"]
        if marker["point"] != point:
            raise RuntimeError("fault marker does not identify the selected boundary")
        if point in BUILD_POINTS:
            commit = record["delivery"]["event"]["after"]
            rows = [
                row
                for row in self.delivery.collection("builds")
                if row["document"]["request"]["source"]["commit"] == commit
            ]
            if len(rows) != 1:
                raise RuntimeError("fault revision does not have one automatic build")
            build_id = rows[0]["id"]
            record["build_id"] = build_id
            job_id = str(uuid5(NAMESPACE_URL, f"goblin-king/pipeline/{build_id}/build/1"))
            expected = (
                record["release_before"]["document"]["environment"]["image_repository"].split(
                    "/", 1
                )[1]
                + "/"
                + build_id
                if point.startswith("registry.")
                else "pipeline-" + build_id + "/"
            )
            matches = (
                marker["identity"] == expected
                if point.startswith("registry.")
                else marker["identity"].startswith(expected)
            )
        else:
            row = next(
                row
                for row in self.delivery.collection("operations")
                if row["id"] == record["operation_id"]
            )
            job_id = row["document"].get("job_id")
            if not job_id:
                raise RuntimeError("fault operation has no durable scheduler identity")
            stable = str(uuid5(NAMESPACE_URL, "goblin-king/execution/" + job_id))
            expected = (
                {job_id}
                if point.startswith(("dispatch.", "acknowledgement."))
                else {record["operation_id"]}
                if point.startswith(("git.", "recording."))
                else {self.delivery.prefix + "-python-test/app"}
                if point.startswith("helm.")
                else {"pipeline-" + stable + "/worker"}
                if point.startswith("kubernetes.")
                else {"pipeline-" + stable, "pipeline-" + record["operation_id"]}
            )
            matches = marker["identity"] in expected
        if not matches:
            raise RuntimeError("fault marker belongs to another operation; no process was stopped")
        record["original_job_id"] = job_id
        record["identity_verified"] = True
        record["execution_before"] = self.execution_identity(job_id)
        self.save(point, record)

    def execution_identity(self, job_id):
        name = "pipeline-" + str(uuid5(NAMESPACE_URL, "goblin-king/execution/" + job_id))
        raw = self.proof.kube(
            "-n",
            self.delivery.control,
            "get",
            "configmap",
            name,
            "--ignore-not-found",
            "-o",
            "json",
        )
        if not raw.strip():
            return None
        ledger = json.loads(raw)
        document = json.loads(ledger["data"]["execution.json"])
        if document["identity"]["job_id"] != job_id:
            raise RuntimeError("retained execution has a conflicting scheduler identity")
        stage = document["stages"].get("worker")
        job = None
        if stage:
            job = json.loads(
                self.proof.kube(
                    "-n",
                    self.delivery.control,
                    "get",
                    "job",
                    stage["name"],
                    "--ignore-not-found",
                    "-o",
                    "json",
                )
                or "null"
            )
        return {
            "ledger_uid": ledger["metadata"]["uid"],
            "document": document,
            "job_uid": job["metadata"]["uid"] if job else None,
        }

    def pods(self, namespace):
        return json.loads(self.proof.kube("-n", namespace, "get", "pods", "-o", "json"))["items"]

    def hit(self, record):
        namespaces = [self.delivery.control]
        if record["point"].startswith("registry."):
            namespaces.append(self.delivery.prefix + "-python-build")
        for namespace in namespaces:
            for pod in self.pods(namespace):
                if pod["status"].get("phase") != "Running":
                    continue
                for container in pod["status"].get("containerStatuses", []):
                    if not container.get("state", {}).get("running"):
                        continue
                    logs = self.proof.kube(
                        "-n",
                        namespace,
                        "logs",
                        pod["metadata"]["name"],
                        "-c",
                        container["name"],
                        "--tail=40",
                        "--limit-bytes=16384",
                    )
                    for line in logs.splitlines():
                        if '"pipeline_fault"' not in line:
                            continue
                        try:
                            marker = json.loads(line)["pipeline_fault"]
                        except (ValueError, KeyError):
                            continue
                        if marker.get("id") == record["id"]:
                            return {
                                "namespace": namespace,
                                "pod": pod,
                                "marker": marker,
                                "container": container["name"],
                            }
        return None

    def terminated(self, hit):
        pod = next(
            (
                p
                for p in self.pods(hit["namespace"])
                if p["metadata"]["uid"] == hit["pod"]["metadata"]["uid"]
            ),
            None,
        )
        if pod is None:
            return {"uid": hit["pod"]["metadata"]["uid"], "absent": True}
        statuses = [
            *pod["status"].get("containerStatuses", []),
            *pod["status"].get("initContainerStatuses", []),
        ]
        count = len(pod["spec"]["containers"]) + len(pod["spec"].get("initContainers", []))
        if (
            pod["status"]["phase"] in {"Succeeded", "Failed"}
            and len(statuses) == count
            and all(item.get("state", {}).get("terminated") for item in statuses)
        ):
            return {"uid": pod["metadata"]["uid"], "absent": False, "status": pod["status"]}
        return None

    def stop(self, hit):
        if self.terminated(hit):
            return
        patch = [
            {"op": "test", "path": "/metadata/uid", "value": hit["pod"]["metadata"]["uid"]},
            {"op": "add", "path": "/spec/activeDeadlineSeconds", "value": 1},
        ]
        self.proof.kube(
            "-n",
            hit["namespace"],
            "patch",
            "pod",
            hit["pod"]["metadata"]["name"],
            "--type=json",
            "-p",
            json.dumps(patch),
        )

    def concurrent_record(self, record):
        script = """
import json,os,subprocess,sys,tempfile
args=json.load(sys.stdin)
root='/data/release-state'
def git(*argv, data=None, env=None):
    return subprocess.check_output(['git','-C',root,*argv],input=data,env=env).decode().strip()
head=git('rev-parse','refs/heads/main')
path='concurrent/'+args['id']+'.json'
content=json.dumps({'fault_id':args['id'],'purpose':'concurrent release recording'})+'\\n'
blob=git('hash-object','-w','--stdin',data=content.encode())
with tempfile.TemporaryDirectory() as temporary:
    env=os.environ | {'GIT_INDEX_FILE':temporary+'/index','GIT_AUTHOR_NAME':'Local acceptance',
        'GIT_AUTHOR_EMAIL':'acceptance@localhost','GIT_COMMITTER_NAME':'Local acceptance',
        'GIT_COMMITTER_EMAIL':'acceptance@localhost'}
    git('read-tree',head,env=env)
    git('update-index','--add','--cacheinfo','100644,'+blob+','+path,env=env)
    tree=git('write-tree',env=env)
    commit=git('commit-tree',tree,'-p',head,'-m','Preserve concurrent release evidence',env=env)
    git('update-ref','refs/heads/main',commit,head)
print(json.dumps({'previous':head,'commit':commit,'path':path,'content':content}))
"""
        return json.loads(
            self.proof.kube(
                "-n",
                self.delivery.provider,
                "exec",
                "-i",
                "deploy/provider",
                "--",
                "python",
                "-c",
                script,
                document={"id": record["id"]},
            )
        )

    def settle_release(self, point, record):
        operation_id = record["operation_id"]
        settled = self.delivery.operation(operation_id)
        if settled["status"] == "held":
            record["held"] = settled
            self.save(point, record)
            if point == "kubernetes.before_create":
                if self.history() != record["history_before"]:
                    raise RuntimeError("ambiguous absent Job caused a replacement Helm operation")
                return settled
            if "recovery" not in record:
                request = record.setdefault(
                    "recovery_request",
                    {
                        "expected_version": settled["version"],
                        "idempotency_key": record["id"] + "-recover",
                    },
                )
                self.save(point, record)
                record["recovery"] = self.delivery.request(
                    f"/pipeline/operations/{operation_id}/recover",
                    request,
                )
                self.save(point, record)
            settled = self.delivery.operation(operation_id)
            if settled["status"] == "held":
                raise RuntimeError(
                    "confirmed terminated operation remains ambiguous after recovery"
                )
            record["reconciled"] = settled
            self.save(point, record)
        if record.get("held") and point != "kubernetes.before_create":
            cleared = self.delivery.operation(
                self.delivery.action(
                    record["id"] + "-clear",
                    "python",
                    "test",
                    "clear-hold",
                )["operation_id"]
            )
            if cleared["status"] != "completed":
                raise RuntimeError("recovered release hold could not be cleared")
            record["clearance"] = cleared
        return settled

    def run_point(self, point):
        path = self.directory / (point + ".json")
        record = (
            json.loads(path.read_text())
            if path.exists()
            else {
                "id": str(uuid4()),
                "point": point,
                "status": "pending",
            }
        )
        if record["status"] == "passed":
            if not record.get("identity_verified"):
                raise RuntimeError(
                    "older fault evidence lacks identity assertions; use a fresh run"
                )
            return record
        if "history_before" not in record:
            self.wait_for_acknowledgements()
            record["release_before"] = self.delivery.release("python", "test")
            record["history_before"] = self.history()
            self.save(point, record)
        if "hit" not in record:
            plan = {"id": record["id"], "point": point}
            if point.startswith("registry."):
                plan["identity_prefix"] = (
                    record["release_before"]["document"]["environment"]["image_repository"].split(
                        "/", 1
                    )[1]
                    + "/"
                )
            self.gate(plan)
            if point in (*BUILD_POINTS, *ROLLBACK_POINTS):
                options = {"fault": "health"} if point in ROLLBACK_POINTS else {}
                record["delivery"] = self.delivery.merge(record["id"], "python", "test", **options)
                self.save(point, record)
                if point in ROLLBACK_POINTS and "operation_id" not in record:
                    build = self.delivery.build(record["delivery"])
                    if build["status"] != "published":
                        raise RuntimeError("rollback fault did not publish the unhealthy fixture")
                    record["build"] = build
                    operation = self.delivery.wait(
                        "rollback fault release operation",
                        lambda: next(
                            (
                                op
                                for op in self.delivery.collection("operations")
                                if op["document"].get("execution", {}).get("build_id")
                                == build["id"]
                            ),
                            None,
                        ),
                    )
                    record["operation_id"] = operation["id"]
                    self.save(point, record)
            elif "operation_id" not in record:
                started = self.delivery.action(record["id"], "python", "test", "activate")
                record["operation_id"] = started["operation_id"]
                self.save(point, record)
            record["hit"] = self.delivery.wait(
                "fault boundary " + point,
                lambda: self.hit(record),
                timeout=720 if point in ROLLBACK_POINTS else 420 if point in BUILD_POINTS else 240,
            )
            self.save(point, record)
        if not record.get("identity_verified"):
            self.bind_hit(record)
        if point == "git.before_refs" and "concurrent" not in record:
            record["concurrent"] = self.concurrent_record(record)
            self.save(point, record)
        if "terminated" not in record:
            self.stop(record["hit"])
            record["terminated"] = self.delivery.wait(
                "original process termination",
                lambda: self.terminated(record["hit"]),
                timeout=120,
            )
            self.save(point, record)
        # Removing the gate before confirmed termination would let the original process continue.
        self.gate({})
        for component in ("api", "scheduler"):
            self.proof.kube(
                "-n",
                self.delivery.control,
                "rollout",
                "status",
                "deployment/delivery-" + component,
                "--timeout=120s",
            )
        if point in BUILD_POINTS:
            record["build"] = self.delivery.build(record["delivery"])
            if record["build"]["status"] not in {"failed", "held"}:
                raise RuntimeError("interrupted publication or sandbox was accepted as successful")
            record["registry"] = self.registry_observation(record)
            expected = 200 if point == "registry.after_manifest" else 404
            if record["registry"]["status"] != expected:
                raise RuntimeError("immutable tag state differs from the interrupted boundary")
        else:
            record["settled"] = self.settle_release(point, record)
        if "history_after" not in record:
            record["history_after"] = self.history()
            record["release_after"] = self.delivery.release("python", "test")
            record["execution_after"] = self.execution_identity(record["original_job_id"])
            self.save(point, record)
        original, final = record["execution_before"], record["execution_after"]
        if original and (
            not final
            or original["ledger_uid"] != final["ledger_uid"]
            or original["job_uid"]
            and original["job_uid"] != final["job_uid"]
        ):
            raise RuntimeError("interruption replaced the original execution identity")
        before, after = record["release_before"]["document"], record["release_after"]["document"]
        if before["observed"]["image"] != after["observed"]["image"]:
            raise RuntimeError("interruption changed the selected image digest")
        if point in BUILD_POINTS and (
            record["history_after"] != record["history_before"]
            or before["recording"] != after["recording"]
        ):
            raise RuntimeError("interrupted build changed the healthy release or its Git record")
        if point != "kubernetes.before_create" and after["operation_id"]:
            raise RuntimeError("release ownership remained unsettled")
        record["health_after"] = self.delivery.route_health(record["release_after"])
        revisions = (
            record["history_after"][-1]["revision"] - record["history_before"][-1]["revision"]
        )
        # A single intended upgrade is allowed; recovery may observe it, never repeat it.
        if revisions > (2 if point in ROLLBACK_POINTS else 1):
            raise RuntimeError("interruption repeated a Helm effect")
        if record.get("concurrent"):
            preserved = self.proof.kube(
                "-n",
                self.delivery.provider,
                "exec",
                "deploy/provider",
                "--",
                "git",
                "-C",
                "/data/release-state",
                "show",
                "main:" + record["concurrent"]["path"],
            )
            if preserved != record["concurrent"]["content"]:
                raise RuntimeError("recording retry discarded the concurrent Git edit")
            record["concurrent_preserved"] = True
        if point in ROLLBACK_POINTS:
            self.save(point, record)
            repaired = self.delivery.build(
                self.delivery.merge(record["id"] + "-repair", "python", "test", fault="repair")
            )
            if repaired["status"] != "published":
                raise RuntimeError("rollback fault repair did not publish")
            record["repaired"] = self.delivery.deployed("python", "test", repaired["id"])
        if point != "kubernetes.before_create":
            self.wait_for_acknowledgements()
        record["status"] = "passed"
        self.save(point, record)
        print(
            json.dumps(
                {
                    "fault": point,
                    "status": "passed",
                    "operation_id": record.get("operation_id"),
                    "build_id": record.get("build", {}).get("id"),
                }
            ),
            flush=True,
        )
        return record

    def registry_observation(self, record):
        script = """
import hashlib,json,sys
from pathlib import Path
from goblin_king.pipeline_oci import RegistryClient
args=json.load(sys.stdin)
root=Path('/var/run/pipeline/registry')
client=RegistryClient('https://registry.gk-local-registry.svc',
    (root/'username').read_text(),(root/'password').read_text(),root/'ca.crt')
status,_,raw=client.request('/v2/'+args['repository']+'/manifests/'+args['tag'],expected=(200,404))
print(json.dumps({'status':status,'digest':'sha256:'+hashlib.sha256(raw).hexdigest()
    if status==200 else None}))
"""
        return json.loads(
            self.proof.kube(
                "-n",
                self.delivery.control,
                "exec",
                "-i",
                "deploy/delivery-api",
                "--",
                "python",
                "-c",
                script,
                document={
                    "repository": record["release_before"]["document"]["environment"][
                        "image_repository"
                    ].split("/", 1)[1],
                    "tag": record["build"]["id"],
                },
            )
        )

    def run(self):
        for point in POINTS:
            self.run_point(point)
        return self.delivery.save(
            "side-effect-faults",
            {
                "points": list(POINTS),
                "evidence_directory": str(self.directory.relative_to(self.proof.output)),
            },
        )


def main():
    from pipeline_acceptance import Acceptance
    from pipeline_delivery_acceptance import DeliveryAcceptance

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--point", choices=POINTS)
    args = parser.parse_args()
    proof = Acceptance(args.output)
    suite = FaultAcceptance(DeliveryAcceptance(proof))
    started = time.monotonic()
    if args.point:
        suite.run_point(args.point)
        print("Development subset only; mandatory scenario is not marked passed.")
    else:
        suite.run()
    print(f"Elapsed seconds: {time.monotonic() - started:.1f}")


if __name__ == "__main__":
    main()
