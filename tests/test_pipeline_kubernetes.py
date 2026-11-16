"""Publication cannot cross an ambiguous sandbox deletion boundary."""

from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
from kubernetes.client.exceptions import ApiException

from goblin_king.pipeline_kubernetes import RecoveryHold, RetainedExecution


def ledger():
    execution = RetainedExecution.__new__(RetainedExecution)
    execution.namespace = "source"
    execution.core, execution.batch, execution.save = Mock(), Mock(), Mock()
    execution.document = {
        "stages": {
            "builder": {"name": "builder", "uid": "original", "status": "complete"},
        }
    }
    execution.core.list_namespaced_pod.return_value = NS(items=[])
    return execution


def test_sandbox_removal_requires_job_and_pod_absence():
    execution = ledger()
    execution.batch.read_namespaced_job.side_effect = [
        NS(metadata=NS(uid="original")),
        ApiException(status=404),
    ]
    execution.remove_sandbox("builder")
    assert execution.document["stages"]["builder"]["sandbox_absent"]
    assert execution.batch.delete_namespaced_job.call_args.kwargs["body"]["preconditions"] == {
        "uid": "original",
    }


def test_missing_pods_do_not_hide_replacement_job():
    execution = ledger()
    execution.batch.read_namespaced_job.side_effect = [
        NS(metadata=NS(uid="original")),
        NS(metadata=NS(uid="replacement")),
    ]
    with pytest.raises(RecoveryHold, match="replaced"):
        execution.remove_sandbox("builder")
    execution.save.assert_not_called()


def test_replayed_removal_rechecks_authoritative_absence():
    execution = ledger()
    execution.document["stages"]["builder"]["status"] = "removed"
    execution.batch.read_namespaced_job.return_value = NS(metadata=NS(uid="new"))
    with pytest.raises(RecoveryHold, match="identity changed"):
        execution.remove_sandbox("builder")
    execution.batch.delete_namespaced_job.assert_not_called()


def test_interrupted_deletion_can_acknowledge_confirmed_absence():
    execution = ledger()
    execution.batch.read_namespaced_job.side_effect = ApiException(status=404)
    execution.remove_sandbox("builder")
    assert execution.document["stages"]["builder"]["status"] == "removed"
