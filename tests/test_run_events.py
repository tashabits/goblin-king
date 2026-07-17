"""Focused compatibility, security, and transport tests for live run events."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from starlette.websockets import WebSocketDisconnect

from goblin_king.auth import create_api_token, create_project, create_user
from goblin_king.contracts import GoblinContext, JobRecord, RunEventEnvelope, RunRecord, utc_now
from goblin_king.kubernetes_runtime import KubernetesRuntime
from goblin_king.run_events import (
    DEFAULT_RUN_EVENT_MAX_PAYLOAD_BYTES,
    RUN_EVENT_CONTRACT_VERSION_ENV,
    RUN_EVENT_MAX_EVENTS_ENV,
    RUN_EVENT_MAX_PAYLOAD_BYTES_ENV,
    RUN_EVENT_MIN_INTERVAL_MS_ENV,
    RUN_EVENT_RATE_KEY_ENV,
    RUN_EVENT_REDIS_URL_ENV,
    RUN_EVENT_SEQUENCE_KEY_ENV,
    RUN_EVENT_STREAM_ENV,
    RUN_EVENT_TTL_SECONDS_ENV,
    RunEventError,
    RunEventPublisher,
    read_run_event_entries,
    worker_run_event_environment,
)
from goblin_king.runtime import DockerRuntime
from goblin_king.workers import WorkerImageDefinition, WorkerImageMap
from tests.api_helpers import build_api_client


class MemoryPipeline:
    """Transaction-shaped adapter over MemoryRedis."""

    def __init__(self, redis: MemoryRedis) -> None:
        self.redis = redis
        self.operations: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def xadd(self, *args: Any, **kwargs: Any) -> MemoryPipeline:
        self.operations.append(("xadd", args, kwargs))
        return self

    def expire(self, *args: Any, **kwargs: Any) -> MemoryPipeline:
        self.operations.append(("expire", args, kwargs))
        return self

    def execute(self) -> list[Any]:
        results = []
        for name, args, kwargs in self.operations:
            results.append(getattr(self.redis, name)(*args, **kwargs))
        return results


class MemoryRedis:
    """Exact bounded Redis Stream double used by publisher and API tests."""

    def __init__(self) -> None:
        self.sequence = 0
        self.rate_active = False
        self.entries: dict[str, list[tuple[str, dict[str, str]]]] = {}
        self.expirations: dict[str, int] = {}
        self.xadd_options: list[dict[str, Any]] = []

    def set(self, _key: str, _value: str, *, nx: bool, px: int) -> bool | None:
        assert nx is True
        assert px >= 50
        if self.rate_active:
            return None
        self.rate_active = True
        return True

    def allow_next_event(self) -> None:
        self.rate_active = False

    def incr(self, _key: str) -> int:
        self.sequence += 1
        return self.sequence

    def pipeline(self, *, transaction: bool) -> MemoryPipeline:
        assert transaction is True
        return MemoryPipeline(self)

    def xadd(
        self,
        stream: str,
        fields: dict[str, str],
        *,
        maxlen: int,
        approximate: bool,
    ) -> str:
        assert approximate is False
        entry_id = f"{self.sequence}-0"
        entries = self.entries.setdefault(stream, [])
        entries.append((entry_id, fields))
        del entries[:-maxlen]
        self.xadd_options.append({"maxlen": maxlen, "approximate": approximate})
        return entry_id

    def expire(self, key: str, seconds: int) -> bool:
        self.expirations[key] = seconds
        return True

    def xrange(
        self,
        stream: str,
        *,
        min: str,
        max: str,
        count: int,
    ) -> list[tuple[str, dict[str, str]]]:
        assert (min, max) == ("-", "+")
        return self.entries.get(stream, [])[:count]


def _publisher(redis: MemoryRedis, *, max_events: int = 256) -> RunEventPublisher:
    environment = worker_run_event_environment("run-1", "redis://example/0")
    return RunEventPublisher(
        redis=redis,
        run_id="run-1",
        stream=environment[RUN_EVENT_STREAM_ENV],
        sequence_key=environment[RUN_EVENT_SEQUENCE_KEY_ENV],
        rate_key=environment[RUN_EVENT_RATE_KEY_ENV],
        max_events=max_events,
    )


def test_publisher_keeps_monotonic_exactly_bounded_replay() -> None:
    """Retain the newest exact window while sequences remain stable across reads."""
    redis = MemoryRedis()
    publisher = _publisher(redis, max_events=2)

    first = publisher.progress(10, "started")
    redis.allow_next_event()
    second = publisher.stdout("working\n")
    redis.allow_next_event()
    third = publisher.progress(100, "done")
    retained = read_run_event_entries(
        "redis://example/0",
        "run-1",
        after_sequence=1,
        redis_factory=lambda _url: redis,
    )

    assert [first.sequence, second.sequence, third.sequence] == [1, 2, 3]
    assert [event.sequence for _entry_id, event in retained] == [2, 3]
    assert redis.xadd_options == [
        {"maxlen": 2, "approximate": False},
        {"maxlen": 2, "approximate": False},
        {"maxlen": 2, "approximate": False},
    ]
    assert set(redis.expirations) == {
        "goblin-king:run-events:run-1",
        "goblin-king:run-events:run-1:sequence",
    }
    assert set(redis.expirations.values()) == {3600}


def test_publisher_enforces_rate_payload_and_run_scoped_keys() -> None:
    """Reject burst and oversized writes before they can consume retained capacity."""
    redis = MemoryRedis()
    publisher = _publisher(redis)

    with pytest.raises(RunEventError, match="unsupported run event type"):
        publisher.emit("unknown", {})  # type: ignore[arg-type]
    with pytest.raises(RunEventError, match="JSON serializable"):
        publisher.emit("message", {"value": object()})
    publisher.stdout("first")
    with pytest.raises(RunEventError, match="one every 50ms"):
        publisher.stdout("burst")
    redis.allow_next_event()
    with pytest.raises(RunEventError, match="payload exceeds"):
        publisher.emit("message", {"text": "x" * DEFAULT_RUN_EVENT_MAX_PAYLOAD_BYTES})

    environment = {
        "GOBLIN_RUN_ID": "run-1",
        **worker_run_event_environment("run-1", "redis://example/0"),
    }
    loaded = RunEventPublisher.from_environment(
        environment,
        redis_factory=lambda _url: MemoryRedis(),
    )
    assert loaded.run_id == "run-1"
    environment[RUN_EVENT_STREAM_ENV] = "goblin-king:run-events:another-run"
    with pytest.raises(RunEventError, match="does not match"):
        RunEventPublisher.from_environment(
            environment,
            redis_factory=lambda _url: MemoryRedis(),
        )


def test_docker_and_kubernetes_workers_receive_the_same_additive_contract(tmp_path: Path) -> None:
    """Keep backend semantics identical without adding an API or scheduler credential."""
    workers = WorkerImageMap(
        {"example.progress": WorkerImageDefinition(context=".", image="progress:local")},
        root=".",
    )
    redis_url = "redis://redis:6379/0"
    context = GoblinContext(
        run_id="run-parity",
        artifact_root=str(tmp_path / "artifacts"),
        metadata={"job_id": "job-parity", "project_id": "project-a"},
    )
    docker = DockerRuntime(workers=workers, redis_url=redis_url, run_root=tmp_path / "runs")
    docker_command = docker._docker_run_command(
        image="progress:local",
        run_dir=tmp_path / "runs" / "run-parity",
        context=context,
        worker_id="worker-run-parity",
        timeout_seconds=30,
    )
    docker_environment = {
        docker_command[index + 1].split("=", 1)[0]: docker_command[index + 1].split("=", 1)[1]
        for index, item in enumerate(docker_command[:-1])
        if item == "-e" and "=" in docker_command[index + 1]
    }
    kubernetes = KubernetesRuntime(workers=workers, redis_url=redis_url)
    manifest = kubernetes._job_manifest(
        name="gk-progress-run-parity",
        config_name="gk-progress-run-parity-input",
        image="progress:local",
        context=context,
        worker_id="k8s-worker-run-parity",
        timeout_seconds=30,
    )
    worker = next(
        container
        for container in manifest["spec"]["template"]["spec"]["containers"]
        if container["name"] == "worker"
    )
    kubernetes_environment = {item["name"]: item["value"] for item in worker["env"]}
    contract_keys = {
        RUN_EVENT_CONTRACT_VERSION_ENV,
        RUN_EVENT_REDIS_URL_ENV,
        RUN_EVENT_STREAM_ENV,
        RUN_EVENT_SEQUENCE_KEY_ENV,
        RUN_EVENT_RATE_KEY_ENV,
        RUN_EVENT_MAX_EVENTS_ENV,
        RUN_EVENT_MAX_PAYLOAD_BYTES_ENV,
        RUN_EVENT_MIN_INTERVAL_MS_ENV,
        RUN_EVENT_TTL_SECONDS_ENV,
    }

    assert {key: docker_environment[key] for key in contract_keys} == {
        key: kubernetes_environment[key] for key in contract_keys
    }
    assert all("TOKEN" not in key and "ADMIN" not in key for key in contract_keys)


def test_authenticated_run_event_read_and_stream_are_project_scoped(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Authorize both replay and live delivery against the run's persisted project."""
    redis = MemoryRedis()
    publisher = _publisher(redis)
    publisher.progress(25, "working")
    monkeypatch.setattr("goblin_king.run_events.Redis.from_url", lambda _url: redis)
    client, store, _artifact_root = build_api_client(tmp_path)
    project_a = create_project(store, name="Project A")
    project_b = create_project(store, name="Project B")
    user = create_user(store, email="worker@example.test", display_name="Worker")
    _token_a, raw_a = create_api_token(
        store,
        name="project-a",
        user_id=user.id,
        project_id=project_a.id,
        role="member",
    )
    _token_b, raw_b = create_api_token(
        store,
        name="project-b",
        user_id=user.id,
        project_id=project_b.id,
        role="member",
    )
    store.save_job(
        JobRecord(
            id="job-1",
            kind="example.progress",
            project_id=project_a.id,
            created_at=utc_now(),
            status="running",
        )
    )
    store.save_run(
        RunRecord(
            id="run-1",
            job_id="job-1",
            kind="example.progress",
            project_id=project_a.id,
            status="running",
            started_at=datetime(2026, 7, 16, tzinfo=UTC),
        )
    )

    unauthenticated = client.get("/runs/run-1/events")
    denied = client.get(
        "/runs/run-1/events",
        headers={"Authorization": f"Bearer {raw_b}"},
    )
    allowed = client.get(
        "/runs/run-1/events",
        headers={"Authorization": f"Bearer {raw_a}"},
    )

    assert unauthenticated.status_code == 401
    assert denied.status_code == 403
    assert allowed.status_code == 200
    assert allowed.json()["next_sequence"] == 1
    assert allowed.json()["items"] == [
        {
            "sequence": 1,
            "created_at": allowed.json()["items"][0]["created_at"],
            "event_type": "progress",
            "run_id": "run-1",
            "payload": {"percent": 25, "message": "working"},
            "job_id": "job-1",
            "project_id": project_a.id,
        }
    ]

    with client.websocket_connect(f"/ws/runs/run-1/events?token={raw_a}") as websocket:
        event = websocket.receive_json()
        assert event["sequence"] == 1
        assert event["project_id"] == project_a.id
    with pytest.raises(WebSocketDisconnect) as denied_websocket:
        with client.websocket_connect(f"/ws/runs/run-1/events?token={raw_b}"):
            pass
    assert denied_websocket.value.code == 1008


def test_reader_discards_foreign_malformed_and_oversized_entries() -> None:
    """Never project unvalidated worker bytes into an authenticated client response."""
    redis = MemoryRedis()
    stream = "goblin-king:run-events:run-1"
    valid = RunEventEnvelope(
        sequence=3,
        created_at=utc_now(),
        event_type="message",
        run_id="run-1",
        payload={"ok": True},
    )
    foreign = valid.model_copy(update={"sequence": 1, "run_id": "run-other"})
    oversized = valid.model_copy(
        update={"sequence": 2, "payload": {"text": "x" * DEFAULT_RUN_EVENT_MAX_PAYLOAD_BYTES}}
    )
    duplicate = valid.model_copy(update={"payload": {"duplicate": True}})
    redis.entries[stream] = [
        ("1-0", {"event": foreign.model_dump_json()}),
        ("2-0", {"event": oversized.model_dump_json()}),
        ("bad-0", {"event": "not-json"}),
        ("3-0", {"event": valid.model_dump_json()}),
        ("4-0", {"event": duplicate.model_dump_json()}),
    ]

    events = read_run_event_entries(
        "redis://example/0",
        "run-1",
        redis_factory=lambda _url: redis,
    )

    assert [(entry_id, event.sequence) for entry_id, event in events] == [("3-0", 3)]
