"""Authenticate bounded Kubernetes identities against retained execution ownership."""

import json
import time
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
    action = "network" if operation["document"].get("action") == "networking" else "deploy"
    expected = f"system:serviceaccount:{namespace}:pipeline-{action}-{environment}"
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
    # A Pod can start before the Job creation response is durably acknowledged.
    # Keep authority closed while that original identity is being recorded.
    deadline = time.monotonic() + 5
    while True:
        ledger = json.loads(
            core.read_namespaced_config_map("pipeline-" + stable_id, namespace).data[
                "execution.json"
            ]
        )
        job_uid = ledger["stages"]["worker"]["uid"]
        if job_uid is not None:
            break
        if time.monotonic() >= deadline:
            raise PipelineConflict("execution Job identity has not been acknowledged")
        time.sleep(0.1)
    pod = core.read_namespaced_pod(names[0], namespace)
    if pod.metadata.uid != uids[0] or pod.metadata.deletion_timestamp:
        raise PipelineConflict("execution Pod identity changed or is terminating")
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


def verify_network_phase_identity(token, operation, stage, core):
    """Bind a nested networking phase to the original retained Job and one live Pod."""
    if not token or len(token) > 16384 or stage not in {"network-before", "network-after"}:
        raise PipelineConflict("approved network phase identity required")
    execution = operation["document"]["execution"]
    if not execution.get("networking") or operation["status"] != "running":
        raise PipelineConflict("network phase has no running parent release")
    namespace = execution["environment"]["namespace"]
    account = execution["environment"]["release"] + "-network"
    status = (
        client.AuthenticationV1Api()
        .create_token_review(client.V1TokenReview(spec=client.V1TokenReviewSpec(token=token)))
        .status
    )
    if (
        not status
        or not status.authenticated
        or status.user.username != f"system:serviceaccount:{namespace}:{account}"
    ):
        raise PipelineConflict("network phase identity is outside its release")
    extra = status.user.extra or {}
    names, uids = (
        extra.get("authentication.kubernetes.io/pod-name", []),
        extra.get("authentication.kubernetes.io/pod-uid", []),
    )
    if len(names) != 1 or len(uids) != 1:
        raise PipelineConflict("network phase identity must be bound to one Pod")
    suffix = (
        str(
            uuid5(
                NAMESPACE_URL,
                execution["operation_id"] + "/recovery/" + str(execution["recovery_attempt"]),
            )
        )
        if execution.get("recover")
        else execution["operation_id"]
    )
    ledger_name = "pipeline-" + suffix
    deadline = time.monotonic() + 5
    while True:
        ledger = json.loads(
            core.read_namespaced_config_map(ledger_name, namespace).data["execution.json"]
        )
        if ledger["identity"]["request"] != execution:
            raise PipelineConflict("network phase parent intent differs from admission")
        identity = ledger.get("stages", {}).get(stage, {})
        request = ledger.get("network_requests", {}).get(stage)
        if identity.get("uid") and request:
            break
        if time.monotonic() >= deadline:
            raise PipelineConflict("network phase identity has not been acknowledged")
        time.sleep(0.1)
    pod = core.read_namespaced_pod(names[0], namespace)
    if (
        pod.metadata.uid != uids[0]
        or pod.metadata.deletion_timestamp
        or pod.spec.service_account_name != account
        or len(pod.spec.containers) != 1
        or pod.spec.containers[0].image != execution["networking"]["image"]
        or not any(
            ref.kind == "Job" and ref.uid == identity["uid"]
            for ref in pod.metadata.owner_references or []
        )
    ):
        raise PipelineConflict("network phase Pod does not match retained authority")
    return {"pod_uid": pod.metadata.uid, "job_uid": identity["uid"], "request": request}


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
            or len(pod.status.init_container_statuses or [])
            != len(getattr(pod.spec, "init_containers", None) or [])
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
        "source_namespace": retained.get("source_namespace"),
    }


def terminated_build(build, core, batch, namespace):
    """A new build UUID is eligible only after all prior execution has stopped."""
    central = terminated_execution(build, core, batch, namespace)
    source_namespace = central.get("source_namespace")
    if not source_namespace:
        raise PipelineConflict("original source namespace is not retained; reconcile fencing")
    prefix = build["id"] + "-"
    try:
        jobs = [
            job
            for job in batch.list_namespaced_job(source_namespace).items
            if job.metadata.name.startswith(prefix)
        ]
        pods = [
            pod
            for pod in core.list_namespaced_pod(source_namespace).items
            if pod.metadata.name.startswith(prefix)
        ]
        for job in jobs:
            if not any(
                condition.status == "True" and condition.type in {"Complete", "Failed"}
                for condition in job.status.conditions or []
            ):
                raise PipelineConflict("a prior source or publication Job is not terminal")
        for pod in pods:
            regular = pod.status.container_statuses or []
            initial = pod.status.init_container_statuses or []
            if (
                pod.status.phase not in {"Succeeded", "Failed"}
                or len(regular) != len(pod.spec.containers)
                or len(initial) != len(pod.spec.init_containers or [])
                or any(
                    item.state is None or item.state.terminated is None
                    for item in [*regular, *initial]
                )
            ):
                raise PipelineConflict("a prior source or publication container has not terminated")
        return central | {
            "build_id": build["id"],
            "source_namespace": source_namespace,
            "source_job_uids": [job.metadata.uid for job in jobs],
            "source_pod_uids": [pod.metadata.uid for pod in pods],
        }
    except ApiException as error:
        raise PipelineConflict("source termination evidence is unavailable") from error
