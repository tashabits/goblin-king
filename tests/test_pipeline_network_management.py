"""Durable networking changes share release fencing and survive database reopen."""

from concurrent.futures import ThreadPoolExecutor

import pytest

from goblin_king.pipeline_network_management import NetworkManagement
from goblin_king.pipeline_networking import NetworkingPolicy
from goblin_king.pipeline_state import PipelineConflict, PipelineState, release_key
from goblin_king.store import SQLiteStore
from goblin_king.store_schema import pipeline_dispatch_table, pipeline_operations_table
from tests.test_pipeline_networking import configuration, policy
from tests.test_pipeline_state import attached, count


def setup(tmp_path):
    state, reg, _ = attached(tmp_path)
    env = reg.environments[0]
    base = policy().model_dump(mode="json")
    allowed = base["releases"].pop("app-test/app")
    allowed["project_id"] = reg.project_id
    base["releases"][env.namespace + "/" + env.release] = allowed
    return NetworkManagement(state, NetworkingPolicy.model_validate(base)), release_key(
        reg, env.name
    )


def arguments(manager, key):
    preview = manager.preview(key, configuration())
    return {
        "expected_version": preview["expected_version"],
        "preview_id": preview["preview_id"],
        "actor": "operator",
        "idempotency_key": "network-test",
    }


def test_preview_and_legacy_state_cannot_launch_or_expose_anything(tmp_path):
    manager, key = setup(tmp_path)
    assert manager.inspect(key)["network_version"] == 0
    assert manager.inspect(key)["desired"]["ingress"]["enabled"] is False
    preview = manager.preview(key, configuration())
    assert preview["active"] is False
    assert preview["external_reachability"] == "unverified"
    assert count(manager.state, pipeline_dispatch_table) == 0
    assert count(manager.state, pipeline_operations_table) == 0


def test_apply_is_atomic_idempotent_and_survives_reopen(tmp_path):
    manager, key = setup(tmp_path)
    args = arguments(manager, key)
    result = manager.apply(key, configuration(), **args)
    other = NetworkManagement(
        PipelineState(SQLiteStore(tmp_path / "state.sqlite3")), manager.policy
    )
    assert other.apply(key, configuration(), **args) == result
    current = other.inspect(key)
    assert current["network_version"] == 1
    assert current["operation_id"] == result["operation_id"]
    assert current["active"] is False
    assert count(manager.state, pipeline_operations_table) == 1
    assert count(manager.state, pipeline_dispatch_table) == 0
    with pytest.raises(PipelineConflict, match="idempotency"):
        other.apply(key, {}, **args)


def test_network_change_and_application_operation_share_exclusion(tmp_path):
    manager, key = setup(tmp_path)
    args = arguments(manager, key)
    manager.state.begin_operation(
        key, expected_version=1, action="deactivate", actor="operator", idempotency_key="deactivate"
    )
    with pytest.raises(PipelineConflict):
        manager.apply(key, configuration(), **args)
    assert manager.inspect(key)["network_version"] == 0


def test_concurrent_network_updates_create_one_operation(tmp_path):
    manager, key = setup(tmp_path)
    args = arguments(manager, key)

    def apply(index):
        try:
            return manager.apply(key, configuration(), **(args | {"idempotency_key": str(index)}))
        except PipelineConflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(apply, [1, 2]))
    assert sum(result is not None for result in results) == 1
    assert count(manager.state, pipeline_operations_table) == 1


def test_changed_installation_policy_invalidates_preview(tmp_path):
    manager, key = setup(tmp_path)
    args = arguments(manager, key)
    manager.policy = manager.policy.model_copy(
        update={"proxy_image": "example/squid@sha256:" + "b" * 64}
    )
    with pytest.raises(PipelineConflict, match="preview"):
        manager.apply(key, configuration(), **args)
    assert manager.inspect(key)["network_version"] == 0


def test_restore_recovery_mode_does_not_allow_network_changes(tmp_path):
    manager, key = setup(tmp_path)
    manager.recovery_mode = True
    with pytest.raises(PipelineConflict, match="recovery"):
        manager.apply(key, configuration(), **arguments(manager, key))


def test_capabilities_do_not_disclose_other_project_or_secret_names(tmp_path):
    manager, _ = setup(tmp_path)
    assert manager.capabilities("other")["gateways"] == {}
    view = manager.capabilities("sample-project")
    assert view["gateways"]["test"]["certificates"] == ["test"]
    assert "test-tls" not in str(view)
