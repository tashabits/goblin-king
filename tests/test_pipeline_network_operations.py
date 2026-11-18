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


def test_policy_change_requires_fresh_preview_for_explicit_recovery(tmp_path):
    from goblin_king.pipeline_network_management import NetworkManagement

    manager, key, operations, operation_id = prepared(tmp_path)
    operations.finish(operation_id, {"status": "failure", "data": {"recovery_hold": True}})
    updated_policy = manager.policy.model_copy(
        update={
            "gateways": {
                "test": manager.policy.gateways["test"].model_copy(update={"probe_port": 8443})
            }
        }
    )
    current = NetworkManagement(manager.state, updated_policy)
    with manager.state.store.engine.connect() as connection:
        operation = manager.state.row(connection, pipeline_operations_table, operation_id)
    recovery = ReleaseOperations(manager.state, network_policy=updated_policy)
    arguments = {
        "expected_version": operation["version"],
        "actor": "operator",
        "termination": {"job_id": operation["document"]["job_id"], "terminated": True},
        "binding": {"image": "example/network@sha256:" + "a" * 64, "resource_policy": {}},
        "idempotency_key": "reconcile",
    }
    with pytest.raises(PipelineConflict, match="preview the held"):
        recovery.recover(operation_id, **arguments)
    preview = current.preview(key, configuration())
    result = recovery.recover(
        operation_id, **arguments, networking_preview_id=preview["preview_id"]
    )
    assert (
        recovery.recover(operation_id, **arguments, networking_preview_id=preview["preview_id"])
        == result
    )
    with pytest.raises(PipelineConflict, match="idempotency"):
        recovery.recover(operation_id, **arguments, networking_preview_id="0" * 64)
    with manager.state.store.engine.connect() as connection:
        recovered = manager.state.row(connection, pipeline_operations_table, operation_id)
    assert recovered["document"]["execution"]["policy_revision"] == updated_policy.revision
    assert recovered["document"]["execution"]["reapproved_policy_from"] == manager.policy.revision


def test_failed_read_only_probe_releases_lock_and_replays_after_version_change(tmp_path):
    manager, key, operations, operation_id = prepared(tmp_path)
    observed = {
        "configuration": manager.inspect(key)["desired"],
        "policy_revision": manager.policy.revision,
        "active": False,
    }
    operations.finish(operation_id, {"status": "success", "data": {"networking": observed}})
    options = arguments(manager, key) | {"idempotency_key": "probe", "probe_only": True}
    result = manager.apply(key, configuration(), **options)
    operations.prepare(
        result["operation_id"],
        {
            "image": "example/network@sha256:" + "a" * 64,
            "resource_policy": {},
        },
    )
    operations.finish(result["operation_id"], {"status": "failure", "error": "health unavailable"})
    diagnostic = next(
        item
        for item in manager.diagnostics("sample-project")["items"]
        if item["release_key"] == key
    )
    assert diagnostic["status"] == "attention"
    assert any("last health probe failed" in action for action in diagnostic["actions"])
    assert manager.diagnostics("another-project")["items"] == []
    assert manager.inspect(key)["operation_id"] is None
    assert manager.inspect(key)["hold"] is None
    assert manager.inspect(key)["observed"] == observed
    assert manager.apply(key, configuration(), **(options | {"preview_id": "f" * 64})) == result
