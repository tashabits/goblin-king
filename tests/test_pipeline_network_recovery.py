"""Restoration cannot enable delivery from stale or writable network observations."""

from types import SimpleNamespace

import pytest

from goblin_king.pipeline_kubernetes import RecoveryHold
from goblin_king.pipeline_network_recovery import ReadOnlyAPI, verify_network_recovery
from tests.test_pipeline_networking import configuration, policy


def test_restoration_transport_rejects_every_mutating_method():
    calls = []
    api = ReadOnlyAPI(SimpleNamespace(call_api=lambda *args, **kwargs: calls.append(args)))
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        with pytest.raises(RecoveryHold, match="cannot mutate"):
            api.call_api("/resource", method, response_types_map={200: "object"})
    assert calls == []
    api.call_api("/resource", "GET", response_types_map={200: "object"})
    assert calls == [("/resource", "GET")]


@pytest.mark.parametrize("problem", ["policy", "desired", "hold", "operation", "activation"])
def test_restoration_requires_current_settled_observation_before_cluster_access(problem):
    installed = policy()
    observed = {
        "configuration": configuration(),
        "policy_revision": installed.revision,
        "active": True,
    }
    network = {
        "observed": observed,
        "desired": configuration(),
        "hold": None,
        "operation_id": None,
        "network_version": 1,
    }
    release = {
        "project_id": "fixture",
        "document": {
            "environment": {"namespace": "app-test", "release": "app", "name": "test"},
            "active": True,
            "observed": {"image": "fixture@sha256:" + "a" * 64},
            "operation_id": None,
        },
    }
    if problem == "policy":
        observed["policy_revision"] = "old"
    elif problem == "desired":
        network["desired"] = {}
    elif problem == "hold":
        network["hold"] = {"reason": "unresolved"}
    elif problem == "operation":
        release["document"]["operation_id"] = "unsettled"
    else:
        observed["active"] = False
    with pytest.raises(RecoveryHold, match="explicit reconciliation"):
        verify_network_recovery(installed, release, network)
