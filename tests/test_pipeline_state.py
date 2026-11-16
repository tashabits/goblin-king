"""Cross-connection admission, submission acknowledgement, and release fencing."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import func, select

from goblin_king.contracts import JobRecord, utc_now
from goblin_king.pipeline_state import PipelineConflict, PipelineState, release_key
from goblin_king.pipeline_store import PipelineAdmissionStore
from goblin_king.store import SQLiteStore
from goblin_king.store_schema import (
    pipeline_build_requests_table,
    pipeline_dispatch_table,
    pipeline_environment_states_table,
    pipeline_journal_table,
)
from tests.test_pipeline_contracts import source_change
from tests.test_pipeline_store import registration


def attached(tmp_path):
    state = PipelineState(SQLiteStore(tmp_path / "state.sqlite3"))
    reg = registration()
    result = state.attach(
        reg,
        expected_version=0,
        repository_id=1,
        installation_id=2,
        discovery={},
        worker={"image": "registry.test/worker@sha256:" + "a" * 64},
        actor="operator",
        idempotency_key="attach",
    )
    return state, reg, result["key"]


def enabled(tmp_path):
    state, reg, key = attached(tmp_path)
    state.management(
        key,
        expected_version=1,
        enabled=True,
        heads={e.branch: "b" * 40 for e in reg.environments},
        actor="operator",
    )
    return state, reg, key


def admit(state, key, **kwargs):
    return state.admit(
        key,
        source_change(**kwargs),
        expected_version=2,
        expected_cursor="b" * 40,
        verification={"pull_request": 1},
    )


def count(state, table):
    with state.store.engine.connect() as connection:
        return connection.scalar(select(func.count()).select_from(table))


def test_attach_enable_and_upgrade_never_dispatch_historical_work(tmp_path):
    state, reg, key = attached(tmp_path)
    PipelineAdmissionStore(state.store).admit(reg, source_change())
    state.management(
        key,
        expected_version=1,
        enabled=True,
        heads={e.branch: "b" * 40 for e in reg.environments},
        actor="operator",
    )
    assert not admit(state, key).created
    assert count(state, pipeline_dispatch_table) == 0
    assert state.store.list_jobs() == []


def test_blocked_dispatch_records_actionable_reason(tmp_path):
    state, _, key = enabled(tmp_path)
    admitted = admit(state, key)

    def reject(job):
        raise PipelineConflict("approved resource policy changed")

    state.dispatch_pending(reject)
    state.dispatch_pending(reject)
    from goblin_king.store_schema import pipeline_builds_table

    with state.store.engine.connect() as connection:
        row = state.row(connection, pipeline_builds_table, admitted.request.build_id)
    assert row["document"]["dispatch_error"] == "approved resource policy changed"
    assert row["version"] == 2
    assert state.store.list_jobs() == []


def test_atomic_admission_rollback_preserves_every_boundary(tmp_path, monkeypatch):
    state, _, key = enabled(tmp_path)
    original = state.audit

    def fail(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("crash before commit")

    before = count(state, pipeline_journal_table)
    monkeypatch.setattr(state, "audit", fail)
    with pytest.raises(RuntimeError, match="crash"):
        admit(state, key)
    assert count(state, pipeline_build_requests_table) == 0
    assert count(state, pipeline_dispatch_table) == 0
    assert count(state, pipeline_journal_table) == before
    for row in state.list(pipeline_environment_states_table, project_id="sample-project"):
        assert row["document"]["generation"] == 0


def test_concurrent_admission_dispatch_once_after_lost_ack(tmp_path, monkeypatch):
    state, _, key = enabled(tmp_path)

    def run(_):
        other = PipelineState(SQLiteStore(state.store.db_path))
        return admit(other, key)

    with ThreadPoolExecutor(max_workers=4) as pool:
        outcomes = list(pool.map(run, range(4)))
    assert sum(o.created for o in outcomes) == 1
    assert len({o.request.build_id for o in outcomes}) == 1
    submit = state.store.submit_job_once

    def lost_ack(job):
        submit(job)
        raise RuntimeError("lost acknowledgement")

    monkeypatch.setattr(state.store, "submit_job_once", lost_ack)
    with pytest.raises(RuntimeError, match="lost acknowledgement"):
        state.dispatch_pending(lambda job: None)
    reopened = PipelineState(SQLiteStore(state.store.db_path))
    reopened.dispatch_pending(lambda job: None)
    assert len(reopened.store.list_jobs()) == 1
    with reopened.store.engine.connect() as connection:
        assert connection.scalar(select(pipeline_dispatch_table.c.status)) == "dispatched"


def test_stable_job_rejects_conflicting_identity_and_historical_ids(tmp_path):
    state, _, _ = attached(tmp_path)
    job = JobRecord(
        id="stable", kind="example.hello", input={"message": "one"}, created_at=utc_now()
    )
    assert state.store.submit_job_once(job)[1]
    assert not state.store.submit_job_once(job)[1]
    with pytest.raises(ValueError, match="conflicts"):
        state.store.submit_job_once(job.model_copy(update={"input": {"message": "two"}}))
    old = job.model_copy(update={"id": "historical"})
    state.store.save_job(old)
    with pytest.raises(ValueError, match="conflicts"):
        state.store.submit_job_once(old)


def test_release_operation_survives_reopen_and_cannot_be_stolen(tmp_path):
    state, reg, _ = enabled(tmp_path)
    key = release_key(reg, "test")
    first = state.begin_operation(
        key, expected_version=1, action="deactivate", actor="operator", idempotency_key="turn-off"
    )
    reopened = PipelineState(SQLiteStore(state.store.db_path))
    assert (
        reopened.begin_operation(
            key,
            expected_version=1,
            action="deactivate",
            actor="operator",
            idempotency_key="turn-off",
        )
        == first
    )
    with pytest.raises(PipelineConflict, match="still owned"):
        reopened.begin_operation(
            key, expected_version=2, action="activate", actor="operator", idempotency_key="turn-on"
        )


def test_validation_failure_leaves_intent_pending_without_job(tmp_path):
    state, _, key = enabled(tmp_path)
    admit(state, key)

    def reject(job):
        raise ValueError("image lacks validation")

    with pytest.raises(ValueError, match="validation"):
        state.dispatch_pending(reject)
    assert state.store.list_jobs() == []


def test_newer_admission_preserves_unacknowledged_existing_execution(tmp_path, monkeypatch):
    state, _, key = enabled(tmp_path)
    first = admit(state, key)
    submit = state.store.submit_job_once

    def crash_after_submit(job):
        submit(job)
        raise RuntimeError("acknowledgement lost")

    monkeypatch.setattr(state.store, "submit_job_once", crash_after_submit)
    with pytest.raises(RuntimeError, match="acknowledgement lost"):
        state.dispatch_pending(lambda job: None)
    state.admit(
        key,
        source_change(commit="d" * 40, delivery_id="new-head"),
        expected_version=2,
        expected_cursor=first.request.source.commit,
        verification={"pull_request": 2},
    )
    monkeypatch.setattr(state.store, "submit_job_once", submit)
    state.dispatch_pending(lambda job: None)
    assert len(state.store.list_jobs()) == 2
    with state.store.engine.connect() as connection:
        old = (
            connection.execute(
                select(pipeline_dispatch_table).where(
                    pipeline_dispatch_table.c.build_id == first.request.build_id
                )
            )
            .mappings()
            .one()
        )
        assert old["status"] == "dispatched"
