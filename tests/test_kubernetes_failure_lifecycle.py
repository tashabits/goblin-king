"""Scheduler lifecycle proof for generated Kubernetes Pod startup failures."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from goblin_king.contracts import GoblinDefinition, GoblinResult, JobRecord, utc_now
from goblin_king.registry import GoblinRegistry
from goblin_king.runtime import KubernetesRuntime
from goblin_king.scheduler import Scheduler
from goblin_king.store import SQLiteStore
from goblin_king.workers import WorkerImageDefinition, WorkerImageMap


class RecordingBatchClient:
    def __init__(self) -> None:
        self.created: list[str] = []
        self.deleted: list[str] = []

    def create_namespaced_job(self, *, body, **_kwargs) -> None:
        self.created.append(body["metadata"]["name"])

    def read_namespaced_job(self, **_kwargs):
        status = (
            SimpleNamespace(succeeded=0, failed=0)
            if len(self.created) == 1
            else SimpleNamespace(succeeded=1, failed=0)
        )
        return SimpleNamespace(status=status)

    def delete_namespaced_job(self, *, name, **_kwargs) -> None:
        self.deleted.append(name)


class PullFailureThenSuccessCoreClient:
    def __init__(self, batch: RecordingBatchClient) -> None:
        self.batch = batch
        self.created: list[str] = []
        self.deleted: list[str] = []

    def create_namespaced_config_map(self, *, body, **_kwargs) -> None:
        self.created.append(body["metadata"]["name"])

    def list_namespaced_pod(self, **_kwargs):
        waiting = SimpleNamespace(
            reason="ImagePullBackOff",
            message="authentication required for configured registry",
        )
        status = SimpleNamespace(
            name="result-forwarder",
            state=SimpleNamespace(waiting=waiting),
        )
        pod = SimpleNamespace(
            metadata=SimpleNamespace(name=f"{self.batch.created[-1]}-pod"),
            status=SimpleNamespace(
                init_container_statuses=None,
                container_statuses=[status],
            ),
        )
        return SimpleNamespace(items=[pod])

    def delete_namespaced_config_map(self, *, name, **_kwargs) -> None:
        self.deleted.append(name)


def test_scheduler_continues_after_pull_failure_and_cleans_transient_objects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("goblin_king.events.EventBus._publish", lambda *_a, **_k: None)
    monkeypatch.setattr("goblin_king.events.EventBus._append_stream", lambda *_a, **_k: None)
    batch = RecordingBatchClient()
    core = PullFailureThenSuccessCoreClient(batch)
    monkeypatch.setattr(
        "goblin_king.kubernetes_runtime.kubernetes_clients",
        lambda: (batch, core),
    )
    monkeypatch.setattr(
        KubernetesRuntime,
        "_load_result",
        lambda _self, _run_id: GoblinResult.ok(data={"ok": True}),
    )
    monkeypatch.setattr(
        KubernetesRuntime,
        "_record_worker_heartbeats",
        lambda _self, _context: None,
    )

    kind = "example.echo"
    definition = GoblinDefinition(
        kind=kind,
        display_name="Echo",
        module="container.only",
    )
    workers = WorkerImageMap(
        {kind: WorkerImageDefinition(context=".", image="echo:local")},
        root=".",
    )
    store = SQLiteStore(tmp_path / "scheduler.sqlite3")
    now = utc_now()
    for job_id in ("job-pull-fails", "job-next-runs"):
        store.save_job(
            JobRecord(
                id=job_id,
                kind=kind,
                input={},
                created_at=now,
                status="queued",
            )
        )
    scheduler = Scheduler(
        registry=GoblinRegistry.from_definitions([definition]),
        store=store,
        runtime_mode="kubernetes",
        workers=workers,
    )
    monkeypatch.setattr(scheduler, "_validate_before_container_run", lambda *_a, **_k: None)

    runs = scheduler.run_once(now)

    assert [run.status for run in runs] == ["failed", "completed"]
    assert "ImagePullBackOff" in (runs[0].error or "")
    assert len([event for event in store.list_events() if event.event_type == "job.failed"]) == 1
    assert len(batch.created) == len(batch.deleted) == 2
    assert len(core.created) == len(core.deleted) == 2
