"""Interrupted publication stays held even when a container prints a plausible result."""

import pytest

from goblin_king.contracts import GoblinResult
from goblin_king.pipeline_runtime import inspected_result


@pytest.mark.parametrize(
    "succeeded,output",
    [(False, GoblinResult.ok().model_dump_json()), (True, ""), (True, "not a result")],
)
def test_interruption_cannot_become_an_eligible_failed_build(succeeded, output):
    result = inspected_result({"succeeded": succeeded, "logs": {"pod/worker": output}})
    assert result.status == "failed"
    assert result.data["recovery_hold"] is True


def test_observed_application_failure_remains_explicitly_rebuildable():
    failure = GoblinResult.failed(error="source tests failed")
    result = inspected_result(
        {
            "succeeded": True,
            "logs": {"pod/worker": failure.model_dump_json(), "pod/result-forwarder": ""},
        }
    )
    assert result == failure
    assert not result.data.get("recovery_hold")
