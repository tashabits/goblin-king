"""Authenticate bounded Kubernetes identities against retained execution ownership."""

import json
from uuid import NAMESPACE_URL, uuid5

from kubernetes import client
from kubernetes.client.exceptions import ApiException

from goblin_king.pipeline_state import PipelineConflict


def verify_execution_identity(token, operation, core, namespace):
    if not token or len(token) > 16384:
        raise PipelineConflict("execution identity required")
    response = client.AuthenticationV1Api().create_token_review(
        client.V1TokenReview(spec=client.V1TokenReviewSpec(token=token))
    )
    status = response.status
    environment = operation["document"]["execution"]["environment"]["name"]
    expected = f"system:serviceaccount:{namespace}:pipeline-deploy-{environment}"
    if not status.authenticated or status.user.username != expected:
        raise PipelineConflict("execution identity is not approved for this environment")
    extra = status.user.extra or {}
    names, uids = (
        extra.get("authentication.kubernetes.io/pod-name", []),
        extra.get("authentication.kubernetes.io/pod-uid", []),
    )
    if len(names) != 1 or len(uids) != 1:
        raise PipelineConflict("execution identity must be bound to one Pod")
    pod = core.read_namespaced_pod(names[0], namespace)
    if pod.metadata.uid != uids[0] or pod.metadata.deletion_timestamp:
        raise PipelineConflict("execution Pod identity changed or is terminating")
    stable_id = str(
        uuid5(NAMESPACE_URL, "goblin-king/execution/" + operation["document"]["job_id"])
    )
    ledger = json.loads(
        core.read_namespaced_config_map("pipeline-" + stable_id, namespace).data["execution.json"]
    )
    job_uid = ledger["stages"]["worker"]["uid"]
    if not any(
        ref.kind == "Job" and ref.uid == job_uid for ref in pod.metadata.owner_references or []
    ):
        raise PipelineConflict("Pod does not belong to the retained execution Job")
    return pod.metadata.uid


def terminated_execution(operation, core, batch, namespace):
    """Require the original Job and every retained Pod to establish actual termination."""
    try:
        return _terminated_execution(operation, core, batch, namespace)
    except PipelineConflict:
        raise
    except (ApiException, KeyError, ValueError) as error:
        raise PipelineConflict(
            "termination evidence is unavailable; restore access or reconcile fencing"
        ) from error


def _terminated_execution(operation, core, batch, namespace):
    job_id = operation["document"]["job_id"]
    stable_id = str(uuid5(NAMESPACE_URL, "goblin-king/execution/" + job_id))
    retained = json.loads(
        core.read_namespaced_config_map("pipeline-" + stable_id, namespace).data["execution.json"]
    )
    identity = retained["stages"]["worker"]
    job = batch.read_namespaced_job(identity["name"], namespace)
    if job.metadata.uid != identity["uid"] or not (job.status.succeeded or job.status.failed):
        raise PipelineConflict("previous execution Job has not terminated")
    pods = core.list_namespaced_pod(namespace, label_selector="job-name=" + identity["name"]).items
    owned = [
        pod
        for pod in pods
        if any(ref.uid == job.metadata.uid for ref in pod.metadata.owner_references or [])
    ]
    if not owned:
        raise PipelineConflict(
            "termination evidence is absent; reconcile fencing before replacement"
        )
    for pod in owned:
        statuses = pod.status.container_statuses or []
        all_statuses = statuses + (pod.status.init_container_statuses or [])
        if (
            len(statuses) != len(pod.spec.containers)
            or not statuses
            or any(item.state is None or item.state.terminated is None for item in all_statuses)
            or pod.status.phase not in {"Succeeded", "Failed"}
        ):
            raise PipelineConflict("previous execution has a non-terminated container")
    return {
        "job_id": job_id,
        "job_uid": job.metadata.uid,
        "terminated": True,
        "pod_uids": [pod.metadata.uid for pod in owned],
    }
