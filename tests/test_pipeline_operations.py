"""Release fencing and observed-state behavior across independent database connections."""

import pytest

from goblin_king.pipeline_operations import ReleaseOperations
from goblin_king.pipeline_state import PipelineConflict, PipelineState, release_key
from goblin_king.store import SQLiteStore
from goblin_king.store_schema import pipeline_builds_table as builds
from goblin_king.store_schema import pipeline_environment_states_table as releases
from goblin_king.store_schema import pipeline_operations_table as operations
from tests.test_pipeline_state import admit, enabled


def prepared(tmp_path):
    state, registration, key = enabled(tmp_path)
    admission = admit(state, key)
    build_id = admission.request.build_id
    with state.transaction() as connection:
        build = state.row(connection, builds, build_id)
        state.cas(
            connection,
            builds,
            build,
            build["document"]
            | {
                "verification": {"contract": {"port": 8080, "health_path": "/health"}},
                "result": {
                    "status": "success",
                    "data": {
                        "image_repository": "registry.test/app",
                        "image_digest": "sha256:" + "a" * 64,
                        "source_revision": admission.request.source.commit,
                    },
                },
            },
            status="published",
        )
        release = state.row(connection, releases, release_key(registration, "test"))
    operation = state.begin_operation(
        release["key"],
        expected_version=release["version"],
        action="deploy",
        actor="operator",
        idempotency_key="deploy",
    )
    manager = ReleaseOperations(state)
    manager.prepare(
        operation["operation_id"],
        {
            "image": "registry.test/deploy@sha256:" + "b" * 64,
            "profile": "restricted-v1",
            "resource_policy": {},
        },
    )
    return state, manager, operation["operation_id"], release["key"]


def test_claim_cannot_be_replaced_after_reopening_database(tmp_path):
    state, manager, operation, key = prepared(tmp_path)
    selected = manager.claim(operation, "pod-one")
    recovered = ReleaseOperations(PipelineState(SQLiteStore(state.store.db_path)))
    assert recovered.claim(operation, "pod-one") == selected
    with pytest.raises(PipelineConflict, match="already claimed"):
        recovered.claim(operation, "pod-two")
    with pytest.raises(PipelineConflict, match="still owned"):
        with state.store.engine.connect() as connection:
            row = state.row(connection, releases, key)
        state.begin_operation(
            key,
            expected_version=row["version"],
            action="deactivate",
            actor="operator",
            idempotency_key="off",
        )


def test_newer_desired_generation_suppresses_unstarted_deploy(tmp_path):
    state, manager, operation, key = prepared(tmp_path)
    with state.transaction() as connection:
        row = state.row(connection, releases, key)
        state.cas(connection, releases, row, row["document"] | {"generation": 99})
    assert manager.claim(operation, "pod-one") == {"superseded": True}
    with state.store.engine.connect() as connection:
        assert state.row(connection, releases, key)["document"]["operation_id"] is None
        assert state.row(connection, operations, operation)["status"] == "superseded"


def test_healthy_rollout_waits_only_for_recording(tmp_path):
    state, manager, operation, key = prepared(tmp_path)
    selected = manager.claim(operation, "pod-one")
    result = {
        "status": "success",
        "data": {
            "release": {
                "status": "success",
                "image": selected["values"]["image"],
                "active": selected["values"]["active"],
                "helm_revision": 1,
                "health": {"status": 200},
            }
        },
    }
    manager.finish(operation, result)
    manager.finish(operation, result)
    with state.store.engine.connect() as connection:
        row = state.row(connection, releases, key)
        assert row["document"]["observed"]["build_id"] == selected["build_id"]
        assert row["document"]["operation_id"] == operation
        assert state.row(connection, operations, operation)["status"] == "recording"


def test_ambiguous_result_preserves_lock_and_hold(tmp_path):
    state, manager, operation, key = prepared(tmp_path)
    manager.claim(operation, "pod-one")
    manager.finish(operation, {"status": "failed", "data": {"recovery_hold": True}})
    with state.store.engine.connect() as connection:
        row = state.row(connection, releases, key)
        assert row["document"]["observed"] is None
        assert row["document"]["operation_id"] == operation
        assert row["document"]["hold"]
        assert state.row(connection, operations, operation)["status"] == "held"


def test_explicit_rebuild_has_new_identity_and_repeat_is_idempotent(tmp_path):
    state, _, _, key = prepared(tmp_path)
    with state.transaction() as connection:
        from goblin_king.store_schema import pipeline_registrations_table as registrations

        release = state.row(connection, releases, key)
        registration = state.row(connection, registrations, release["registration_key"])
        state.cas(
            connection,
            registrations,
            registration,
            registration["document"]
            | {
                "worker": registration["document"]["worker"]
                | {"resource_policy": {}, "profile": "restricted-v1"}
            },
        )
        original = state.row(connection, builds, release["document"]["desired_build"])
    result = state.rebuild(
        original["id"],
        expected_version=original["version"],
        actor="operator",
        idempotency_key="retry-once",
    )
    assert result["build_id"] != original["id"]
    assert (
        state.rebuild(
            original["id"],
            expected_version=original["version"],
            actor="operator",
            idempotency_key="retry-once",
        )
        == result
    )
    with state.store.engine.connect() as connection:
        assert (
            state.row(connection, releases, key)["document"]["desired_build"] == result["build_id"]
        )


def test_recovery_requires_confirmed_termination_and_retains_exclusive_claim(tmp_path):
    state, manager, operation_id, key = prepared(tmp_path)
    manager.claim(operation_id, "original-pod")
    manager.finish(operation_id, {"status": "failed", "data": {"recovery_hold": True}})
    with state.store.engine.connect() as connection:
        operation = state.row(connection, operations, operation_id)
    binding = {
        "image": "registry.test/deploy@sha256:" + "b" * 64,
        "profile": "restricted-v1",
        "resource_policy": {},
    }
    arguments = dict(
        expected_version=operation["version"],
        actor="operator",
        binding=binding,
        idempotency_key="recover-one",
    )
    with pytest.raises(PipelineConflict, match="termination"):
        manager.recover(operation_id, termination={"terminated": False}, **arguments)
    termination = {
        "job_id": operation["document"]["job_id"],
        "terminated": True,
        "pod_uids": ["original-pod"],
    }
    recovered = manager.recover(operation_id, termination=termination, **arguments)
    assert recovered["job_id"] != operation["document"]["job_id"]
    assert manager.recover(operation_id, termination={}, **arguments) == recovered
    payload = manager.claim(operation_id, "inspection-pod")
    assert payload["recover"]
    with pytest.raises(PipelineConflict, match="already claimed"):
        manager.claim(operation_id, "replacement-pod")


def test_settled_automatic_attempt_does_not_block_later_reconciliation(tmp_path):
    state, _, _, key = prepared(tmp_path)
    with state.transaction() as connection:
        row = state.row(connection, releases, key)
        state.cas(connection, releases, row, row["document"] | {"operation_id": None})
        row = state.row(connection, releases, key)
    args = dict(
        action="deploy",
        actor="pipeline",
        idempotency_key="automatic-" + row["document"]["desired_build"],
    )
    first = state.begin_operation(key, expected_version=row["version"], automatic=True, **args)
    with state.transaction() as connection:
        row = state.row(connection, releases, key)
        state.cas(connection, releases, row, row["document"] | {"operation_id": None})
        row = state.row(connection, releases, key)
    assert (
        state.begin_operation(key, expected_version=row["version"], automatic=True, **args) == first
    )
    with pytest.raises(PipelineConflict, match="idempotency key conflicts"):
        state.begin_operation(key, expected_version=row["version"], **args)
