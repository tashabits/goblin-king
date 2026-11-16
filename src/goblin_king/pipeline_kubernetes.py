"""Retained Kubernetes execution identities for resumable trusted pipeline stages."""

from __future__ import annotations

import copy
import hashlib
import json
import time

from kubernetes.client import ApiException, V1ConfigMap, V1ObjectMeta


class RecoveryHold(RuntimeError):
    """External ownership or effects cannot be established safely."""


class RetainedExecution:
    """Record intent before Job creation and retain observations until explicit acknowledgement."""

    def __init__(self, core, batch, namespace, operation_id, identity):
        self.core, self.batch, self.namespace = core, batch, namespace
        self.name = f"pipeline-{operation_id}"
        self.identity = identity
        try:
            self.record = core.read_namespaced_config_map(self.name, namespace)
        except ApiException as error:
            if error.status != 404:
                raise
            document = {"identity": identity, "stages": {}, "result": None, "acknowledged": False}
            try:
                self.record = core.create_namespaced_config_map(
                    namespace,
                    V1ConfigMap(
                        metadata=V1ObjectMeta(
                            name=self.name, labels={"goblin-king.io/operation": operation_id}
                        ),
                        data={"execution.json": json.dumps(document)},
                    ),
                )
            except ApiException as conflict:
                if conflict.status != 409:
                    raise
                self.record = core.read_namespaced_config_map(self.name, namespace)
        self.document = json.loads(self.record.data["execution.json"])
        if self.document["identity"] != identity:
            raise RecoveryHold("operation identity conflicts with retained execution")

    def save(self):
        self.record.data["execution.json"] = json.dumps(self.document, sort_keys=True)
        try:
            self.record = self.core.replace_namespaced_config_map(
                self.name, self.namespace, self.record
            )
        except ApiException as error:
            if error.status == 409:
                raise RecoveryHold("another worker owns the execution version") from None
            raise

    def job(self, stage, manifest, *, timeout):
        manifest = copy.deepcopy(manifest)
        fingerprint = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
        manifest["metadata"].setdefault("annotations", {})["goblin-king.io/fingerprint"] = (
            fingerprint
        )
        name = manifest["metadata"]["name"]
        previous = self.document["stages"].get(stage)
        if previous and previous["fingerprint"] != fingerprint:
            raise RecoveryHold("stage payload conflicts with its recorded intent")
        if previous and previous["status"] in {"complete", "removed"}:
            return previous["observation"]
        try:
            job = self.batch.read_namespaced_job(name, self.namespace)
        except ApiException as error:
            if error.status != 404:
                raise
            if previous:
                raise RecoveryHold(
                    "recorded stage Job is absent; refusing an ambiguous replacement"
                ) from None
            self.document["stages"][stage] = {
                "name": name,
                "fingerprint": fingerprint,
                "status": "prepared",
                "uid": None,
            }
            self.save()
            job = self.batch.create_namespaced_job(self.namespace, manifest)
        if job.metadata.annotations.get("goblin-king.io/fingerprint") != fingerprint:
            raise RecoveryHold("Job fingerprint does not match the operation")
        stage_record = self.document["stages"].setdefault(
            stage, {"name": name, "fingerprint": fingerprint, "status": "prepared", "uid": None}
        )
        if stage_record["uid"] and stage_record["uid"] != job.metadata.uid:
            raise RecoveryHold("Job identity changed")
        stage_record.update(uid=job.metadata.uid, status="running")
        self.save()
        deadline = time.monotonic() + timeout + 30
        while True:
            job = self.batch.read_namespaced_job(name, self.namespace)
            if job.metadata.uid != stage_record["uid"]:
                raise RecoveryHold("Job was replaced during execution")
            if job.status.succeeded or job.status.failed:
                pods = self.core.list_namespaced_pod(
                    self.namespace, label_selector=f"job-name={name}"
                ).items
                owned = [
                    pod
                    for pod in pods
                    if any(
                        ref.uid == job.metadata.uid for ref in pod.metadata.owner_references or []
                    )
                ]
                if not owned:
                    raise RecoveryHold("terminal Job has no retained Pod evidence")
                identities = []
                logs = {}
                for pod in owned:
                    statuses = pod.status.container_statuses or []
                    init_statuses = pod.status.init_container_statuses or []
                    if (
                        len(statuses) != len(pod.spec.containers)
                        or len(init_statuses) != len(pod.spec.init_containers or [])
                        or pod.status.phase not in {"Succeeded", "Failed"}
                        or any(
                            item.state.terminated is None for item in [*statuses, *init_statuses]
                        )
                    ):
                        raise RecoveryHold("terminal Job still has a non-terminated container")
                    identities.append(
                        {
                            "name": pod.metadata.name,
                            "uid": pod.metadata.uid,
                            "node": pod.spec.node_name,
                            "containers": [
                                {
                                    "name": item.name,
                                    "image_id": item.image_id,
                                    "exit_code": item.state.terminated.exit_code,
                                    "finished_at": str(item.state.terminated.finished_at),
                                }
                                for item in statuses
                            ],
                        }
                    )
                    for container in statuses:
                        response = self.core.read_namespaced_pod_log(
                            pod.metadata.name,
                            self.namespace,
                            container=container.name,
                            limit_bytes=16384,
                            _preload_content=False,
                        )
                        try:
                            logs[f"{pod.metadata.name}/{container.name}"] = response.read().decode(
                                "utf-8", errors="replace"
                            )
                        finally:
                            response.close()
                            response.release_conn()
                observation = {
                    "job_name": name,
                    "job_uid": job.metadata.uid,
                    "succeeded": bool(job.status.succeeded),
                    "pods": identities,
                    "logs": logs,
                }
                stage_record.update(status="complete", observation=observation)
                self.save()
                return observation
            if time.monotonic() > deadline:
                raise RecoveryHold("stage deadline elapsed without confirmed container termination")
            time.sleep(1)

    def remove_sandbox(self, stage):
        record = self.document["stages"][stage]
        if record["status"] not in {"complete", "removed"}:
            raise RecoveryHold("sandbox must be observed terminated before removal")
        try:
            job = self.batch.read_namespaced_job(record["name"], self.namespace)
            if job.metadata.uid != record["uid"] or record["status"] == "removed":
                raise RecoveryHold("sandbox identity changed before deletion")
            self.batch.delete_namespaced_job(
                record["name"],
                self.namespace,
                body={"propagationPolicy": "Foreground", "preconditions": {"uid": record["uid"]}},
            )
        except ApiException as error:
            if error.status != 404:
                raise
        deadline = time.monotonic() + 60
        while True:
            pods = self.core.list_namespaced_pod(
                self.namespace, label_selector=f"job-name={record['name']}"
            ).items
            try:
                job = self.batch.read_namespaced_job(record["name"], self.namespace)
                if job.metadata.uid != record["uid"]:
                    raise RecoveryHold("sandbox Job was replaced during deletion")
            except ApiException as error:
                if error.status != 404:
                    raise
                if not pods:
                    break
            if time.monotonic() > deadline:
                raise RecoveryHold("sandbox Job or Pods remain; publisher cannot start")
            time.sleep(1)
        record["status"] = "removed"
        record["sandbox_absent"] = True
        self.save()

    def finish(self, result):
        if self.document["result"] is not None and self.document["result"] != result:
            raise RecoveryHold("operation already has a different durable result")
        self.document["result"] = result
        self.save()


def restricted_job(
    name,
    namespace,
    image,
    command,
    *,
    volumes=(),
    mounts=(),
    node=None,
    timeout=900,
    trust="trusted",
    pull_secret=None,
):
    """Fixed non-root helper/test profile without source-provided Pod fragments."""
    spec = {
        "automountServiceAccountToken": False,
        "restartPolicy": "Never",
        "securityContext": {"runAsUser": 65532, "runAsGroup": 1000, "fsGroup": 1000},
        "containers": [
            {
                "name": "step",
                "image": image,
                "command": command,
                "resources": {
                    "requests": {"cpu": "100m", "memory": "128Mi"},
                    "limits": {"cpu": "1", "memory": "1Gi"},
                },
                "securityContext": {
                    "runAsNonRoot": True,
                    "readOnlyRootFilesystem": True,
                    "allowPrivilegeEscalation": False,
                    "capabilities": {"drop": ["ALL"]},
                    "seccompProfile": {"type": "RuntimeDefault"},
                },
                "volumeMounts": [*mounts, {"name": "tmp", "mountPath": "/tmp"}],
            }
        ],
        "volumes": [*volumes, {"name": "tmp", "emptyDir": {"sizeLimit": "512Mi"}}],
    }
    if node:
        spec["nodeSelector"] = {"kubernetes.io/hostname": node}
        spec["tolerations"] = [
            {
                "key": "node-role.kubernetes.io/control-plane",
                "operator": "Exists",
                "effect": "NoSchedule",
            }
        ]
    if pull_secret:
        spec["imagePullSecrets"] = [{"name": pull_secret}]
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {"name": name, "namespace": namespace},
        "spec": {
            "backoffLimit": 0,
            "activeDeadlineSeconds": timeout,
            "template": {"metadata": {"labels": {"goblin-king.io/trust": trust}}, "spec": spec},
        },
    }
