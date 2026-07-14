"""Focused tests for writable Docker worker runtime paths."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from goblin_king.contracts import GoblinDefinition, GoblinResult
from goblin_king.docker_runtime_paths import (
    DEFAULT_DOCKER_RUN_ROOT,
    DockerRuntimePathError,
    relative_to_docker_data_root,
    resolve_docker_artifact_root,
    resolve_docker_run_root,
)
from goblin_king.events import EventBus
from goblin_king.resource_policies import ResourcePolicy
from goblin_king.runtime import DockerRuntime, new_run_context
from goblin_king.store import SQLiteStore
from goblin_king.workers import WorkerImageDefinition, WorkerImageMap


def test_run_root_defaults_only_without_a_named_data_volume() -> None:
    """Keep the local default while requiring explicit container volume placement."""
    assert resolve_docker_run_root(environment={}) == DEFAULT_DOCKER_RUN_ROOT
    with pytest.raises(DockerRuntimePathError, match="GOBLIN_KING_RUN_ROOT is required"):
        resolve_docker_run_root(environment={"GOBLIN_KING_DOCKER_DATA_VOLUME": "shared-data"})


def test_named_data_volume_requires_an_absolute_writable_run_root(tmp_path: Path) -> None:
    """Reject cwd-relative roots that become read-only in hardened scheduler containers."""
    environment = {
        "GOBLIN_KING_DOCKER_DATA_VOLUME": "shared-data",
        "GOBLIN_KING_RUN_ROOT": str(tmp_path / "data" / "runs"),
    }
    assert resolve_docker_run_root(environment=environment) == tmp_path / "data" / "runs"

    environment["GOBLIN_KING_RUN_ROOT"] = ".goblin-king/runs"
    with pytest.raises(DockerRuntimePathError, match="must be absolute"):
        resolve_docker_run_root(environment=environment)


def test_relative_artifacts_follow_the_configured_data_root(tmp_path: Path) -> None:
    """Map legacy relative artifact paths beside runs instead of below the process cwd."""
    run_root = tmp_path / "writable-data" / "runs"
    artifact_root = resolve_docker_artifact_root(
        run_root,
        Path(".goblin-king") / "artifacts" / "job-1",
    )

    assert artifact_root == (tmp_path / "writable-data" / "artifacts" / "job-1").resolve()
    assert (
        relative_to_docker_data_root(
            artifact_root,
            run_root,
            label="artifact directory",
        )
        == "artifacts/job-1"
    )


def test_runtime_setup_failure_becomes_a_failed_result(tmp_path: Path, monkeypatch) -> None:
    """Keep filesystem errors inside the worker result boundary."""
    workers = WorkerImageMap.from_definitions(
        {
            "example.setup-failure": WorkerImageDefinition(
                context=tmp_path,
                image="setup-failure:local",
            )
        },
        root=tmp_path,
    )
    runtime = DockerRuntime(workers=workers, run_root=tmp_path / "data" / "runs")

    def fail_prepare(*_args, **_kwargs):
        raise OSError(30, "Read-only file system")

    monkeypatch.setattr(runtime, "_prepare_run_dir", fail_prepare)
    result = runtime.run(
        GoblinDefinition(
            kind="example.setup-failure",
            display_name="Setup failure",
            module="container.only",
        ),
        None,
        {},
        new_run_context("job-1", "example.setup-failure"),
    )

    assert result.status == "failed"
    assert "Docker runtime setup failed" in (result.error or "")
    assert "Read-only file system" in (result.error or "")


def test_runtime_launch_failure_records_terminal_worker_event(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Pair a started worker event with failure when the Docker client cannot launch."""
    workers = WorkerImageMap.from_definitions(
        {
            "example.launch-failure": WorkerImageDefinition(
                context=tmp_path,
                image="launch-failure:local",
            )
        },
        root=tmp_path,
    )
    store = SQLiteStore(tmp_path / "events.sqlite3")
    event_bus = EventBus(store=store)
    monkeypatch.setattr(event_bus, "_publish", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(event_bus, "_append_stream", lambda *_args, **_kwargs: None)
    runtime = DockerRuntime(
        workers=workers,
        run_root=tmp_path / "data" / "runs",
        event_bus=event_bus,
    )

    def fail_launch(*_args, **_kwargs):
        raise FileNotFoundError(2, "Docker executable not found")

    monkeypatch.setattr("goblin_king.runtime.subprocess.run", fail_launch)
    result = runtime.run(
        GoblinDefinition(
            kind="example.launch-failure",
            display_name="Launch failure",
            module="container.only",
        ),
        None,
        {},
        new_run_context("job-1", "example.launch-failure"),
    )

    assert result.status == "failed"
    assert "Docker runtime launch failed" in (result.error or "")
    events = store.list_events()
    assert [event.event_type for event in events] == ["worker.started", "worker.failed"]
    assert events[-1].payload["phase"] == "launch"


@pytest.mark.parametrize(
    ("filesystem_policy", "artifacts", "metrics", "expected_error"),
    [
        (
            {"artifact_max_files": 1},
            [
                {"name": "one.txt", "uri": "one.txt"},
                {"name": "two.txt", "uri": "two.txt"},
            ],
            {},
            "artifact file count exceeds policy: 2 > 1",
        ),
        (
            {"artifact_max_bytes": 4},
            [{"name": "large.txt", "uri": "large.txt"}],
            {"artifact.large.txt.bytes": 5},
            "artifact bytes exceed policy: 5 > 4",
        ),
    ],
)
def test_artifact_policy_rejection_records_terminal_worker_event(
    tmp_path: Path,
    monkeypatch,
    filesystem_policy: dict[str, int],
    artifacts: list[dict[str, str]],
    metrics: dict[str, int],
    expected_error: str,
) -> None:
    """Pair a started worker event with failure when artifact policy rejects its result."""
    kind = "example.artifact-policy"
    workers = WorkerImageMap.from_definitions(
        {
            kind: WorkerImageDefinition(
                context=tmp_path,
                image="artifact-policy:local",
            )
        },
        root=tmp_path,
    )
    store = SQLiteStore(tmp_path / "events.sqlite3")
    event_bus = EventBus(store=store)
    monkeypatch.setattr(event_bus, "_publish", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(event_bus, "_append_stream", lambda *_args, **_kwargs: None)
    runtime = DockerRuntime(
        workers=workers,
        run_root=tmp_path / "data" / "runs",
        event_bus=event_bus,
    )
    worker_result = GoblinResult.ok(
        data={"preserved": True},
        artifacts=artifacts,
        metrics=metrics,
    )
    monkeypatch.setattr(
        "goblin_king.runtime.subprocess.run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    monkeypatch.setattr(runtime, "_record_worker_heartbeats", lambda *_args: None)
    monkeypatch.setattr(runtime, "_load_result", lambda *_args: worker_result)

    result = runtime.run(
        GoblinDefinition(
            kind=kind,
            display_name="Artifact policy",
            module="container.only",
        ),
        None,
        {},
        new_run_context("job-1", kind),
        resource_policy=ResourcePolicy(filesystem=filesystem_policy),
    )

    assert result == GoblinResult.failed(error=expected_error, data={"preserved": True})
    events = store.list_events()
    lifecycle_events = [
        event
        for event in events
        if event.event_type
        in {
            "worker.started",
            "worker.completed",
            "worker.failed",
            "worker.timed_out",
            "worker.no_result",
        }
    ]
    assert [event.event_type for event in lifecycle_events] == [
        "worker.started",
        "worker.failed",
    ]
    assert lifecycle_events[-1].payload == {
        "kind": kind,
        "phase": "artifact_policy",
        "error": expected_error,
    }
