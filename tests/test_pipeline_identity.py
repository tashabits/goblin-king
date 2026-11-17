"""Termination must be established from the retained Job and all Pod containers."""

import json
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
from kubernetes.client.exceptions import ApiException

from goblin_king.pipeline_identity import (
    terminated_build,
    terminated_execution,
    verify_execution_identity,
)
from goblin_king.pipeline_state import PipelineConflict


def claim_identity(monkeypatch, ledgers):
    core = Mock()
    pod = NS(
        metadata=NS(
            uid="pod-uid", deletion_timestamp=None, owner_references=[NS(kind="Job", uid="job-uid")]
        )
    )
    core.read_namespaced_pod.return_value = pod
    core.read_namespaced_config_map.side_effect = [
        NS(data={"execution.json": json.dumps({"stages": {"worker": {"uid": uid}}})})
        for uid in ledgers
    ]
    authentication = Mock()
    authentication.create_token_review.return_value = NS(
        status=NS(
            authenticated=True,
            user=NS(
                username="system:serviceaccount:control:pipeline-deploy-test",
                extra={
                    "authentication.kubernetes.io/pod-name": ["execution-pod"],
                    "authentication.kubernetes.io/pod-uid": ["pod-uid"],
                },
            ),
        )
    )
    monkeypatch.setattr(
        "goblin_king.pipeline_identity.client.AuthenticationV1Api", lambda: authentication
    )
    operation = {
        "document": {"job_id": "operation-job", "execution": {"environment": {"name": "test"}}}
    }
    return core, pod, operation


def test_claim_waits_for_original_job_identity_acknowledgement(monkeypatch):
    core, _, operation = claim_identity(monkeypatch, [None, "job-uid"])
    assert verify_execution_identity("bound-token", operation, core, "control") == "pod-uid"
    assert core.read_namespaced_config_map.call_count == 2


def test_claim_rejects_recorded_identity_mismatch_without_waiting(monkeypatch):
    core, _, operation = claim_identity(monkeypatch, ["another-job"])
    with pytest.raises(PipelineConflict, match="does not belong"):
        verify_execution_identity("bound-token", operation, core, "control")
    assert core.read_namespaced_config_map.call_count == 1


def test_claim_stays_closed_when_identity_acknowledgement_never_arrives(monkeypatch):
    core, _, operation = claim_identity(monkeypatch, [None])
    ticks = iter([0, 5])
    monkeypatch.setattr("goblin_king.pipeline_identity.time.monotonic", lambda: next(ticks))
    with pytest.raises(PipelineConflict, match="has not been acknowledged"):
        verify_execution_identity("bound-token", operation, core, "control")
    assert core.read_namespaced_config_map.call_count == 1


def test_claim_rechecks_live_pod_after_identity_acknowledgement(monkeypatch):
    core, pod, operation = claim_identity(monkeypatch, [None, "job-uid"])
    terminating = NS(metadata=NS(uid="pod-uid", deletion_timestamp="deleting"))
    core.read_namespaced_pod.side_effect = [pod, terminating]
    with pytest.raises(PipelineConflict, match="terminating"):
        verify_execution_identity("bound-token", operation, core, "control")


def evidence():
    core, batch = Mock(), Mock()
    core.read_namespaced_config_map.return_value = NS(
        data={
            "execution.json": json.dumps(
                {"stages": {"worker": {"name": "execution", "uid": "job-uid"}}}
            )
        }
    )
    batch.read_namespaced_job.return_value = NS(
        metadata=NS(uid="job-uid"), status=NS(succeeded=1, failed=0)
    )
    pod = NS(
        metadata=NS(uid="pod-uid", owner_references=[NS(uid="job-uid")]),
        spec=NS(containers=[NS(name="execution")]),
        status=NS(
            phase="Succeeded",
            container_statuses=[NS(state=NS(terminated=NS(exit_code=0)))],
            init_container_statuses=[],
        ),
    )
    core.list_namespaced_pod.return_value = NS(items=[pod])
    return core, batch, pod


def test_termination_requires_retained_identity_and_every_container():
    core, batch, pod = evidence()
    operation = {"document": {"job_id": "operation-job"}}
    assert terminated_execution(operation, core, batch, "control")["terminated"]
    pod.status.init_container_statuses = [NS(state=NS(terminated=None))]
    with pytest.raises(PipelineConflict, match="non-terminated"):
        terminated_execution(operation, core, batch, "control")
    pod.status.init_container_statuses = []
    batch.read_namespaced_job.return_value.metadata.uid = "replacement"
    with pytest.raises(PipelineConflict, match="has not terminated"):
        terminated_execution(operation, core, batch, "control")
    core.read_namespaced_config_map.side_effect = ApiException(status=404)
    with pytest.raises(PipelineConflict, match="unavailable"):
        terminated_execution(operation, core, batch, "control")


def test_build_recovery_requires_source_jobs_and_all_containers_to_stop():
    core, batch, central = evidence()
    retained = json.loads(core.read_namespaced_config_map.return_value.data["execution.json"])
    retained["source_namespace"] = "source"
    core.read_namespaced_config_map.return_value.data["execution.json"] = json.dumps(retained)
    source = NS(
        metadata=NS(name="build-1-publish-pod", uid="publication-pod"),
        spec=NS(containers=[NS(name="publish")], init_containers=[NS(name="init")]),
        status=NS(
            phase="Succeeded",
            container_statuses=[NS(state=NS(terminated=NS(exit_code=0)))],
            init_container_statuses=[],
        ),
    )
    job = NS(
        metadata=NS(name="build-1-publish", uid="publication-job"),
        status=NS(conditions=[]),
    )
    batch.list_namespaced_job.return_value = NS(items=[job])
    core.list_namespaced_pod.side_effect = lambda namespace, **_: NS(
        items=[central] if namespace == "control" else [source]
    )
    build = {"id": "build-1", "document": {"job_id": "original-job"}}

    with pytest.raises(PipelineConflict, match="Job is not terminal"):
        terminated_build(build, core, batch, "control")
    job.status.conditions = [NS(type="Complete", status="True")]
    with pytest.raises(PipelineConflict, match="container has not terminated"):
        terminated_build(build, core, batch, "control")
    source.status.init_container_statuses = [NS(state=NS(terminated=NS(exit_code=0)))]
    proof = terminated_build(build, core, batch, "control")
    assert proof["job_id"] == "original-job"
    assert proof["source_job_uids"] == ["publication-job"]
    assert proof["source_pod_uids"] == ["publication-pod"]
    source.status.phase = "Running"
    with pytest.raises(PipelineConflict, match="container has not terminated"):
        terminated_build(build, core, batch, "control")
    batch.list_namespaced_job.side_effect = ApiException(status=403)
    with pytest.raises(PipelineConflict, match="unavailable"):
        terminated_build(build, core, batch, "control")
