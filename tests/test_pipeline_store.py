"""Durable pipeline admission survives retries, concurrent controllers, and restart."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import create_engine, func, select

from goblin_king.contracts import JobRecord, utc_now
from goblin_king.pipeline_contracts import PipelineRegistration
from goblin_king.pipeline_store import PipelineAdmissionStore, PipelineEventConflict
from goblin_king.store import SQLiteStore
from goblin_king.store_schema import (
    jobs_table,
    metadata,
    pipeline_build_requests_table,
    pipeline_receipts_table,
)
from tests.test_pipeline_contracts import registration_data, source_change


def admission_store(tmp_path):
    return PipelineAdmissionStore(SQLiteStore(tmp_path / "pipeline.sqlite3"))


def registration(**kwargs):
    return PipelineRegistration.model_validate(registration_data(**kwargs))


def test_first_admission_snapshots_identity_without_scheduling_work(tmp_path):
    store = admission_store(tmp_path)
    outcome = store.admit(registration(), source_change())
    request = outcome.request
    assert outcome.created
    assert request.build_id
    assert request.image_tag == request.build_id
    assert request.environment.name == "test"
    assert request.environment.active is False
    assert request.source.commit == "c" * 40
    assert request.registration.pipeline_revision == "a" * 40
    assert request.created_at.tzinfo is not None
    assert store.store.list_jobs() == []
    assert store.get(request.build_id, project_id="sample-project") == request
    assert store.get(request.build_id, project_id="another-project") is None


def test_replays_and_distinct_deliveries_of_same_commit_share_one_request(tmp_path):
    store = admission_store(tmp_path)
    first = store.admit(registration(), source_change())
    assert not store.admit(registration(), source_change()).created
    duplicate = store.admit(registration(), source_change(delivery_id="delivery-2"))
    assert not duplicate.created
    assert duplicate.request == first.request
    with store.store.engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(pipeline_receipts_table)) == 2
        assert (
            connection.scalar(select(func.count()).select_from(pipeline_build_requests_table)) == 1
        )


@pytest.mark.parametrize("change", [{"commit": "d" * 40}, {"branch": "prod"}, {"actor": "another"}])
def test_reused_delivery_id_with_changed_payload_is_rejected(tmp_path, change):
    store = admission_store(tmp_path)
    original = store.admit(registration(), source_change())
    with pytest.raises(PipelineEventConflict):
        store.admit(registration(), source_change(**change))
    assert store.list_requests(project_id="sample-project") == [original.request]


def test_new_commits_and_environments_get_distinct_build_ids(tmp_path):
    store = admission_store(tmp_path)
    outcomes = [
        store.admit(registration(), change)
        for change in [
            source_change(),
            source_change(delivery_id="delivery-2", commit="d" * 40),
            source_change(delivery_id="delivery-3", branch="prod"),
        ]
    ]
    assert len({outcome.request.build_id for outcome in outcomes}) == 3
    assert all(outcome.created for outcome in outcomes)


def test_config_change_does_not_rewrite_an_admitted_request(tmp_path):
    store = admission_store(tmp_path)
    first = store.admit(registration(), source_change())
    updated = registration_data(catalog_revision="e" * 40)
    updated["environments"][0]["active"] = True
    duplicate = store.admit(
        PipelineRegistration.model_validate(updated), source_change(delivery_id="delivery-2")
    )
    assert duplicate.request == first.request
    assert not duplicate.created


def test_restart_keeps_admission_and_replay_identity(tmp_path):
    first_store = admission_store(tmp_path)
    original = first_store.admit(registration(), source_change()).request
    first_store.store.engine.dispose()
    restarted = admission_store(tmp_path)
    assert restarted.get(original.build_id, project_id="sample-project") == original
    assert restarted.admit(registration(), source_change()).request == original


@pytest.mark.parametrize("same_delivery", [True, False])
def test_concurrent_admission_from_separate_connections_has_one_winner(tmp_path, same_delivery):
    stores = [admission_store(tmp_path) for _ in range(6)]
    barrier = Barrier(len(stores))

    def admit(index):
        barrier.wait(timeout=10)
        delivery = "delivery-1" if same_delivery else f"delivery-{index}"
        return stores[index].admit(registration(), source_change(delivery_id=delivery))

    with ThreadPoolExecutor(max_workers=len(stores)) as pool:
        results = list(pool.map(admit, range(len(stores))))
    assert sum(result.created for result in results) == 1
    assert len({result.request.build_id for result in results}) == 1
    assert len(stores[0].list_requests(project_id="sample-project")) == 1


def test_failure_between_request_and_receipt_rolls_back_both(tmp_path, monkeypatch):
    store = admission_store(tmp_path)
    original = store._save_receipt

    def fail(*args, **kwargs):
        raise RuntimeError("injected persistence failure")

    monkeypatch.setattr(store, "_save_receipt", fail)
    with pytest.raises(RuntimeError, match="injected"):
        store.admit(registration(), source_change())
    assert store.list_requests(project_id="sample-project") == []
    monkeypatch.setattr(store, "_save_receipt", original)
    assert store.admit(registration(), source_change()).created


def test_unregistered_source_leaves_no_receipt_or_request(tmp_path):
    store = admission_store(tmp_path)
    for change in [source_change(repository="unknown/app"), source_change(branch="main")]:
        with pytest.raises(ValueError):
            store.admit(registration(), change)
    assert store.list_requests(project_id="sample-project") == []
    with store.store.engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(pipeline_receipts_table)) == 0


def test_listing_is_bounded_scoped_and_ordered_by_durable_sequence(tmp_path):
    store = admission_store(tmp_path)
    requests = [
        store.admit(
            registration(),
            source_change(delivery_id=f"delivery-{index}", commit=str(index + 1) * 40),
        ).request
        for index in range(3)
    ]
    assert store.list_requests(project_id="sample-project", limit=2) == requests[:2]
    assert (
        store.list_requests(project_id="sample-project", after_sequence=requests[1].sequence)
        == requests[2:]
    )
    assert store.list_requests(project_id="another-project") == []
    for limit in [0, -1, 1001]:
        with pytest.raises(ValueError):
            store.list_requests(project_id="sample-project", limit=limit)


def test_existing_database_gains_pipeline_tables_without_losing_jobs(tmp_path):
    """Simulate the pre-admission schema, then verify additive startup and ordinary jobs."""
    path = tmp_path / "pipeline.sqlite3"
    engine = create_engine(f"sqlite:///{path}")
    metadata.create_all(
        engine,
        tables=[
            table
            for table in metadata.sorted_tables
            if table.name not in {"pipeline_build_requests", "pipeline_receipts"}
        ],
    )
    job = JobRecord(id="existing-job", kind="example.echo", input={}, created_at=utc_now())
    with engine.begin() as connection:
        connection.execute(
            jobs_table.insert().values(
                id=job.id,
                kind=job.kind,
                input_json="{}",
                created_at=job.created_at,
                created_by=job.created_by,
            )
        )
    engine.dispose()
    reopened = admission_store(tmp_path)
    assert reopened.admit(registration(), source_change()).created
    assert reopened.store.get_job(job.id) == job
