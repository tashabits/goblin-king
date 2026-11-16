"""Termination must be established from the retained Job and all Pod containers."""

import json
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
from kubernetes.client.exceptions import ApiException

from goblin_king.pipeline_identity import terminated_execution
from goblin_king.pipeline_state import PipelineConflict


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
