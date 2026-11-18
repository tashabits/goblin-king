"""Network dispatch retains release ownership and cannot publish invented observations."""

import pytest

from goblin_king.pipeline_network_operations import NetworkOperations
from goblin_king.pipeline_operations import ReleaseOperations
from goblin_king.pipeline_state import PipelineConflict
from goblin_king.store_schema import pipeline_dispatch_table, pipeline_operations_table
from tests.test_pipeline_network_management import arguments, setup
from tests.test_pipeline_networking import configuration
from tests.test_pipeline_state import count


def prepared(tmp_path):
    manager, key = setup(tmp_path)
    result = manager.apply(key, configuration(), **arguments(manager, key))
    operations = NetworkOperations(manager.state, manager.policy)
    operations.prepare(
        result["operation_id"],
        {
            "image": "example/network@sha256:" + "a" * 64,
            "profile": "restricted-v1",
            "resource_policy": {},
        },
    )
    return manager, key, operations, result["operation_id"]


def test_prepare_uses_stable_job_and_does_not_activate_unreleased_app(tmp_path):
    manager, key, operations, operation_id = prepared(tmp_path)
    operations.prepare(operation_id, {})
    assert count(manager.state, pipeline_dispatch_table) == 1
    with manager.state.store.engine.connect() as connection:
        operation = manager.state.row(connection, pipeline_operations_table, operation_id)
    assert operation["document"]["execution"]["active"] is False
    claim = ReleaseOperations(manager.state).claim(operation_id, "pod-one")
    assert claim["configuration"] == manager.inspect(key)["desired"]
    with pytest.raises(PipelineConflict):
        ReleaseOperations(manager.state).claim(operation_id, "pod-two")


def test_failure_retains_release_lock_and_creates_recovery_hold(tmp_path):
    manager, key, operations, operation_id = prepared(tmp_path)
    operations.finish(operation_id, {"status": "failure", "data": {"recovery_hold": True}})
    assert manager.inspect(key)["operation_id"] == operation_id
    assert manager.inspect(key)["hold"]["reason"] == "ambiguous networking execution"


def test_success_for_unreleased_app_preserves_inactive_state_and_needs_no_build(tmp_path):
    manager, key, operations, operation_id = prepared(tmp_path)
    observed = {
        "configuration": manager.inspect(key)["desired"],
        "policy_revision": manager.policy.revision,
        "active": False,
        "resources": [],
    }
    result = {"status": "success", "data": {"networking": observed}}
    operations.finish(operation_id, result)
    operations.finish(operation_id, result)
    assert manager.inspect(key)["observed"] == observed
    assert manager.inspect(key)["operation_id"] is None
    assert manager.inspect(key)["active"] is False


def test_unapproved_observation_does_not_clear_ownership(tmp_path):
    manager, key, operations, operation_id = prepared(tmp_path)
    with pytest.raises(PipelineConflict, match="observed"):
        operations.finish(operation_id, {"status": "success", "data": {"networking": {}}})
    assert manager.inspect(key)["operation_id"] == operation_id
