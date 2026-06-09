"""Docker-required integration tests for Phase 3 worker execution."""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import pytest
from redis import Redis

from goblin_king.contracts import GoblinDefinition
from goblin_king.runtime import DockerRuntime, new_run_context
from goblin_king.workers import WorkerImageMap

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
