"""Docker-required integration tests for Phase 3 worker execution."""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import pytest
from redis import Redis

from goblin_king.contracts import GoblinDefinition
from goblin_king.events import EventBus
from goblin_king.registry import GoblinRegistry
from goblin_king.runtime import DockerRuntime, new_run_context
from goblin_king.store import SQLiteStore
from goblin_king.validation import validate_workers
from goblin_king.workers import WorkerImageDefinition, WorkerImageMap

REDIS_CONTAINER = "goblin-king-test-redis"
REDIS_PORT = 6380
REDIS_URL = f"redis://localhost:{REDIS_PORT}/0"


@pytest.fixture(scope="session", autouse=True)
def docker_required() -> None:
    """Require a working local Docker daemon for Phase 3 validation."""
    subprocess.run(["docker", "version"], check=True, capture_output=True, text=True)


@pytest.fixture(scope="session")
def redis_container() -> str:
    """Run a disposable Redis container for Docker result transport tests."""
    subprocess.run(["docker", "rm", "-f", REDIS_CONTAINER], check=False, capture_output=True)
    subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            REDIS_CONTAINER,
            "-p",
            f"{REDIS_PORT}:6379",
            "redis:7-alpine",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    client = Redis.from_url(REDIS_URL)
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            if client.ping():
                break
        except Exception:
            time.sleep(0.25)
    else:
        raise AssertionError("Redis test container did not become ready")

    yield REDIS_CONTAINER
    subprocess.run(["docker", "rm", "-f", REDIS_CONTAINER], check=False, capture_output=True)


@pytest.fixture(scope="session")
def example_worker_image(redis_container: str) -> str:
    """Build the self-contained example.echo worker image with Docker."""
    del redis_container
    worker_map = WorkerImageMap.from_path("goblin-images.json")
    runtime = DockerRuntime(workers=worker_map, redis_url=REDIS_URL)
    runtime.build_image("example.echo")
    return worker_map.get("example.echo").image


def test_docker_runtime_executes_example_worker(
    tmp_path: Path,
    redis_container: str,
    example_worker_image: str,
) -> None:
    """Verify DockerRuntime receives a Redis result and fallback result file from a worker."""
    del redis_container, example_worker_image
    worker_map = WorkerImageMap.from_path("goblin-images.json")
    runtime = DockerRuntime(workers=worker_map, redis_url=REDIS_URL, run_root=tmp_path / "runs")
    context = new_run_context("job-1", "example.echo")
    context = context.model_copy(update={"artifact_root": str(tmp_path / "artifacts")})
    definition = GoblinDefinition(
        kind="example.echo",
        display_name="Echo",
        module="unused.by.docker",
    )

    result = runtime.run(definition, None, {"message": "hello docker"}, context)
    result_file = tmp_path / "runs" / context.run_id / "result.json"
    redis_payload = Redis.from_url(REDIS_URL).get(f"goblin-king:results:{context.run_id}")

    assert result.status == "success"
    assert result.data["message"] == "hello docker"
    assert result_file.exists()
    assert redis_payload is not None
    assert json.loads(redis_payload)["status"] == "success"


def test_docker_runtime_records_worker_heartbeats(
    tmp_path: Path,
    redis_container: str,
    example_worker_image: str,
) -> None:
    """Verify DockerRuntime persists worker heartbeats emitted through Redis."""
    del redis_container, example_worker_image
    store = SQLiteStore(tmp_path / "goblin.sqlite3")
    event_bus = EventBus(store=store, redis_url=REDIS_URL)
    worker_map = WorkerImageMap.from_path("goblin-images.json")
    runtime = DockerRuntime(
        workers=worker_map,
        redis_url=REDIS_URL,
        run_root=tmp_path / "runs",
        event_bus=event_bus,
    )
    context = new_run_context("job-1", "example.echo")
    context = context.model_copy(update={"artifact_root": str(tmp_path / "artifacts")})
    definition = GoblinDefinition(
        kind="example.echo",
        display_name="Echo",
        module="unused.by.docker",
    )

    result = runtime.run(definition, None, {"message": "hello heartbeat"}, context)
    heartbeats = store.list_heartbeats()
    event_types = [event.event_type for event in store.list_events()]

    assert result.status == "success"
    assert heartbeats[0].owner_type == "worker"
    assert heartbeats[0].status == "completed"
    assert heartbeats[0].run_id == context.run_id
    assert "worker.started" in event_types
    assert "worker.completed" in event_types


def test_worker_validation_reports_missing_result_json(
    tmp_path: Path,
    redis_container: str,
) -> None:
    """Verify adopter validation catches containers that produce no result file."""
    del redis_container
    worker_dir = tmp_path / "missing-result"
    worker_dir.mkdir()
    (worker_dir / "Dockerfile").write_text(
        'FROM python:3.12-slim\nCMD ["python", "-c", "print(\\\"no result\\\")"]\n',
        encoding="utf-8",
    )
    registry = GoblinRegistry.from_definitions(
        [
            GoblinDefinition(
                kind="adopter.missing-result",
                display_name="Missing Result",
                module="goblin_king.container_only",
            )
        ]
    )
    workers = WorkerImageMap.from_definitions(
        {
            "adopter.missing-result": WorkerImageDefinition(
                context=worker_dir,
                image="goblin-validation-missing-result:local",
            )
        }
    )

    results = validate_workers(
        registry=registry,
        workers=workers,
        input_payload={},
        build=True,
        redis_url=REDIS_URL,
    )

    assert results[0].ok is False
    assert "produced no result" in (results[0].error or "")


def test_worker_validation_reports_invalid_result_json(
    tmp_path: Path,
    redis_container: str,
) -> None:
    """Verify adopter validation catches invalid result envelopes."""
    del redis_container
    worker_dir = tmp_path / "invalid-result"
    worker_dir.mkdir()
    (worker_dir / "Dockerfile").write_text(
        (
            "FROM python:3.12-slim\n"
            "CMD [\"python\", \"-c\", \"import os; "
            "open(os.environ['GOBLIN_RESULT_PATH'], 'w').write('not json')\"]\n"
        ),
        encoding="utf-8",
    )
    registry = GoblinRegistry.from_definitions(
        [
            GoblinDefinition(
                kind="adopter.invalid-result",
                display_name="Invalid Result",
                module="goblin_king.container_only",
            )
        ]
    )
    workers = WorkerImageMap.from_definitions(
        {
            "adopter.invalid-result": WorkerImageDefinition(
                context=worker_dir,
                image="goblin-validation-invalid-result:local",
            )
        }
    )

    results = validate_workers(
        registry=registry,
        workers=workers,
        input_payload={},
        build=True,
        redis_url=REDIS_URL,
    )

    assert results[0].ok is False
    assert "result envelope invalid" in (results[0].error or "")


def test_event_bus_records_malformed_worker_heartbeat(tmp_path: Path) -> None:
    """Verify malformed worker heartbeat payloads become durable failure events."""
    store = SQLiteStore(tmp_path / "goblin.sqlite3")
    event_bus = EventBus(store=store, redis_url=REDIS_URL)

    event_bus.record_worker_heartbeat_payload("not-json")

    events = store.list_events(event_type="worker.heartbeat_invalid")
    assert len(events) == 1
    assert "error" in events[0].payload


def test_docker_runtime_returns_failure_for_missing_image_mapping(tmp_path: Path) -> None:
    """Verify missing image configuration becomes a failed goblin result."""
    image_map = tmp_path / "goblin-images.json"
    image_map.write_text(json.dumps({"workers": {}}), encoding="utf-8")
    runtime = DockerRuntime(
        workers=WorkerImageMap.from_path(image_map),
        redis_url=REDIS_URL,
        run_root=tmp_path / "runs",
    )
    definition = GoblinDefinition(
        kind="example.echo",
        display_name="Echo",
        module="unused.by.docker",
    )

    result = runtime.run(definition, None, {}, new_run_context("job-1", "example.echo"))

    assert result.status == "failed"
    assert "missing Docker worker image mapping" in (result.error or "")
