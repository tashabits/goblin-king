"""Docker-required integration tests for Phase 3 worker execution."""

from __future__ import annotations

import json
import subprocess
import threading
import time
from pathlib import Path

import pytest
from redis import Redis

from goblin_king.contracts import GoblinDefinition, GoblinResult
from goblin_king.events import EventBus
from goblin_king.registry import GoblinRegistry
from goblin_king.resource_policies import ResourcePolicy
from goblin_king.run_events import read_run_event_entries
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


@pytest.fixture(scope="session")
def example_artifact_worker_image(redis_container: str) -> str:
    """Build the self-contained example.artifact worker image with Docker."""
    del redis_container
    worker_map = WorkerImageMap.from_path("goblin-images.json")
    runtime = DockerRuntime(workers=worker_map, redis_url=REDIS_URL)
    runtime.build_image("example.artifact")
    return worker_map.get("example.artifact").image


@pytest.fixture(scope="session")
def example_progress_worker_image(redis_container: str) -> str:
    """Build the fixed progress worker that exercises live run events."""
    del redis_container
    worker_map = WorkerImageMap.from_path("goblin-images.json")
    runtime = DockerRuntime(workers=worker_map, redis_url=REDIS_URL)
    runtime.build_image("example.progress")
    return worker_map.get("example.progress").image


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


def test_docker_worker_events_are_replayable_before_exit(
    tmp_path: Path,
    redis_container: str,
    example_progress_worker_image: str,
) -> None:
    """Prove fixed-worker progress is visible while DockerRuntime remains blocked in the run."""
    del redis_container, example_progress_worker_image
    worker_map = WorkerImageMap.from_path("goblin-images.json")
    runtime = DockerRuntime(workers=worker_map, redis_url=REDIS_URL, run_root=tmp_path / "runs")
    context = new_run_context("job-progress-live", "example.progress")
    context = context.model_copy(update={"artifact_root": str(tmp_path / "artifacts")})
    definition = GoblinDefinition(
        kind="example.progress",
        display_name="Progress",
        module="unused.by.docker",
    )
    completed: dict[str, object] = {}

    def run_worker() -> None:
        completed["result"] = runtime.run(
            definition,
            None,
            {"steps": 12, "delay_seconds": 0.1},
            context,
        )

    worker_thread = threading.Thread(target=run_worker, daemon=True)
    worker_thread.start()
    observed_while_running = []
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and worker_thread.is_alive():
        observed_while_running = read_run_event_entries(REDIS_URL, context.run_id)
        if any(event.event_type == "progress" for _entry_id, event in observed_while_running):
            break
        time.sleep(0.05)
    was_running_when_observed = worker_thread.is_alive()
    worker_thread.join(timeout=15)
    assert not worker_thread.is_alive()
    retained = read_run_event_entries(REDIS_URL, context.run_id)
    result = completed["result"]

    assert isinstance(result, GoblinResult)
    assert was_running_when_observed is True
    assert any(event.event_type == "stdout" for _entry_id, event in observed_while_running)
    assert any(event.event_type == "progress" for _entry_id, event in observed_while_running)
    assert result.status == "success"
    sequences = [event.sequence for _entry_id, event in retained]
    assert sequences == sorted(set(sequences))
    assert any(
        event.event_type == "progress" and event.payload["percent"] == 100
        for _entry_id, event in retained
    )
    assert retained[-1][1].payload == {"text": "progress run completed\n"}


def test_docker_runtime_passes_project_env_and_secret_refs(
    tmp_path: Path,
    redis_container: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify Docker workers can read project env and secretRefs without echoing values."""
    del redis_container
    worker_dir = tmp_path / "env-worker"
    worker_dir.mkdir()
    (worker_dir / "Dockerfile").write_text(
        "FROM python:3.12-slim\nCOPY worker.py /worker.py\nCMD [\"python\", \"/worker.py\"]\n",
        encoding="utf-8",
    )
    (worker_dir / "worker.py").write_text(
        (
            "import json, os\n"
            "from pathlib import Path\n"
            "result = {\n"
            "    'status': 'success',\n"
            "    'data': {\n"
            "        'mode': os.environ.get('PROJECT_MODE'),\n"
            "        'secret_present': 'PROJECT_SECRET' in os.environ,\n"
            "        'secret_length': len(os.environ.get('PROJECT_SECRET', '')),\n"
            "    },\n"
            "    'artifacts': [],\n"
            "    'metrics': {},\n"
            "    'handoff': [],\n"
            "    'error': None,\n"
            "}\n"
            "Path(os.environ['GOBLIN_RESULT_PATH']).write_text(\n"
            "    json.dumps(result), encoding='utf-8'\n"
            ")\n"
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("PROJECT_SECRET", "secret-value")
    workers = WorkerImageMap.from_definitions(
        {
            "adopter.env-secret": WorkerImageDefinition(
                context=worker_dir,
                image="goblin-env-secret-proof:local",
            )
        }
    )
    runtime = DockerRuntime(workers=workers, redis_url=REDIS_URL, run_root=tmp_path / "runs")
    runtime.build_image("adopter.env-secret")
    context = new_run_context("job-env-secret", "adopter.env-secret")
    context = context.model_copy(update={"artifact_root": str(tmp_path / "artifacts")})
    definition = GoblinDefinition(
        kind="adopter.env-secret",
        display_name="Env Secret",
        module="unused.by.docker",
        metadata={"env": {"PROJECT_MODE": "integration"}, "secret_refs": ["PROJECT_SECRET"]},
    )

    result = runtime.run(definition, None, {}, context)

    assert result.status == "success"
    assert result.data == {
        "mode": "integration",
        "secret_present": True,
        "secret_length": len("secret-value"),
    }
    assert "secret-value" not in result.model_dump_json()


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


def test_docker_runtime_artifact_policy_rejection_emits_terminal_failure(
    tmp_path: Path,
    redis_container: str,
    example_artifact_worker_image: str,
) -> None:
    """Prove a real artifact worker pairs its start with terminal policy failure."""
    del redis_container, example_artifact_worker_image
    store = SQLiteStore(tmp_path / "goblin.sqlite3")
    event_bus = EventBus(store=store, redis_url=REDIS_URL)
    worker_map = WorkerImageMap.from_path("goblin-images.json")
    runtime = DockerRuntime(
        workers=worker_map,
        redis_url=REDIS_URL,
        run_root=tmp_path / "runs",
        event_bus=event_bus,
    )
    context = new_run_context("job-artifact-policy", "example.artifact")
    context = context.model_copy(update={"artifact_root": str(tmp_path / "artifacts")})
    definition = GoblinDefinition(
        kind="example.artifact",
        display_name="Artifact",
        module="unused.by.docker",
    )

    result = runtime.run(
        definition,
        None,
        {"body": "policy proof"},
        context,
        resource_policy=ResourcePolicy(filesystem={"artifact_max_files": 0}),
    )

    assert result.status == "failed"
    assert result.error == "artifact file count exceeds policy: 1 > 0"
    lifecycle_events = [
        event
        for event in store.list_events()
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
        "kind": "example.artifact",
        "phase": "artifact_policy",
        "error": result.error,
    }


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
