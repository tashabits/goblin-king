"""Runtime adapters execute goblin definitions and normalize their result envelopes."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse
from uuid import uuid4

from redis import Redis
from redis.exceptions import RedisError

from goblin_king.contracts import GoblinContext, GoblinDefinition, GoblinResult
from goblin_king.events import (
    DEFAULT_HEARTBEAT_CHANNEL,
    DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
    EventBus,
    worker_heartbeat_key,
)
from goblin_king.workers import WorkerConfigError, WorkerImageMap


class InProcessRuntime:
    """Execute trusted goblin code directly in the current Python process for Phase 1."""

    def run(
        self,
        definition: GoblinDefinition,
        entrypoint: Callable[[dict[str, Any], GoblinContext], Any],
        input_payload: dict[str, Any],
        context: GoblinContext,
    ) -> GoblinResult:
        """Call a goblin entrypoint and convert supported return values into GoblinResult."""
        try:
            raw_result = entrypoint(input_payload, context)
        except Exception as error:  # pragma: no cover - exact exception type is goblin-owned
            return GoblinResult.failed(error=f"{definition.kind} failed: {error}")

        if isinstance(raw_result, GoblinResult):
            return raw_result
        if isinstance(raw_result, dict):
            return GoblinResult.ok(data=raw_result)
        return GoblinResult.failed(
            error=f"{definition.kind} returned unsupported result type {type(raw_result).__name__}"
        )


class DockerRuntime:
    """Execute goblins in per-worker Docker images and collect Redis/file results."""

    def __init__(
        self,
        *,
        workers: WorkerImageMap,
        redis_url: str = "redis://localhost:6379/0",
        run_root: str | Path = Path(".goblin-king") / "runs",
        docker_executable: str = "docker",
        event_bus: EventBus | None = None,
        heartbeat_interval_seconds: int = DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
    ) -> None:
        self.workers = workers
        self.redis_url = redis_url
        self.run_root = Path(run_root)
        self.docker_executable = docker_executable
        self.event_bus = event_bus
        self.heartbeat_interval_seconds = heartbeat_interval_seconds

    def build_image(self, kind: str) -> None:
        """Build one worker image from its configured self-contained worker folder."""
        worker = self.workers.get(kind)
        context = self.workers.resolved_context(worker)
        dockerfile = context / worker.dockerfile
        if not context.exists():
            raise WorkerConfigError(f"worker context does not exist for {kind!r}: {context}")
        if not dockerfile.exists():
            raise WorkerConfigError(f"worker Dockerfile does not exist for {kind!r}: {dockerfile}")
        command = [
            self.docker_executable,
            "build",
            "-t",
            worker.image,
            "-f",
            str(dockerfile),
            str(context),
        ]
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if completed.returncode != 0:
            raise WorkerConfigError(
                f"failed to build worker image for {kind!r}: {completed.stderr.strip()}"
            )

    def run(
        self,
        definition: GoblinDefinition,
        _entrypoint: Callable[[dict[str, Any], GoblinContext], Any] | None,
        input_payload: dict[str, Any],
        context: GoblinContext,
        *,
        timeout_seconds: int | None = None,
    ) -> GoblinResult:
        """Run one Docker worker and normalize the result transported through Redis or file."""
        try:
            worker = self.workers.get(definition.kind)
        except WorkerConfigError as error:
            return GoblinResult.failed(error=str(error))

        run_dir = self._prepare_run_dir(context, input_payload)
        result_path = run_dir / "result.json"
        worker_id = f"worker-{context.run_id}"
        self._emit_worker_event("worker.started", context, worker_id, {"kind": definition.kind})
        command = self._docker_run_command(
            image=worker.image,
            run_dir=run_dir,
            context=context,
            worker_id=worker_id,
            timeout_seconds=timeout_seconds,
        )
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            self._record_worker_heartbeats(context)
            self._emit_worker_event(
                "worker.timed_out",
                context,
                worker_id,
                {"kind": definition.kind, "timeout_seconds": timeout_seconds},
            )
            return GoblinResult.failed(
                error=f"{definition.kind} exceeded timeout_seconds={timeout_seconds}"
            )

        self._record_worker_heartbeats(context)
        result = self._load_result(context.run_id, result_path)
        if result is not None:
            self._emit_worker_event(
                "worker.completed",
                context,
                worker_id,
                {"kind": definition.kind, "status": result.status},
            )
            return result
        if completed.returncode != 0:
            self._emit_worker_event(
                "worker.failed",
                context,
                worker_id,
                {"kind": definition.kind, "exit_code": completed.returncode},
            )
            return GoblinResult.failed(
                error=(
                    f"{definition.kind} Docker worker exited {completed.returncode}: "
                    f"{completed.stderr.strip() or completed.stdout.strip()}"
                )
            )
        self._emit_worker_event("worker.no_result", context, worker_id, {"kind": definition.kind})
        return GoblinResult.failed(error=f"{definition.kind} Docker worker produced no result")

    def _prepare_run_dir(self, context: GoblinContext, input_payload: dict[str, Any]) -> Path:
        """Write worker input/context files and create artifact/result directories."""
        run_dir = (self.run_root / context.run_id).resolve()
        artifact_root = Path(context.artifact_root)
        if not artifact_root.is_absolute():
            artifact_root = (Path.cwd() / artifact_root).resolve()
        run_dir.mkdir(parents=True, exist_ok=True)
        artifact_root.mkdir(parents=True, exist_ok=True)
        (run_dir / "input.json").write_text(json.dumps(input_payload), encoding="utf-8")
        (run_dir / "context.json").write_text(context.model_dump_json(), encoding="utf-8")
        return run_dir

    def _docker_run_command(
        self,
        *,
        image: str,
        run_dir: Path,
        context: GoblinContext,
        worker_id: str,
        timeout_seconds: int | None,
    ) -> list[str]:
        """Compose a deterministic docker run command for a worker container."""
        artifact_root = Path(context.artifact_root)
        if not artifact_root.is_absolute():
            artifact_root = (Path.cwd() / artifact_root).resolve()
        command = [
            self.docker_executable,
            "run",
            "--rm",
            "--label",
            "goblin-king.worker=true",
            "--label",
            f"goblin-king.run-id={context.run_id}",
            "--add-host",
            "host.docker.internal:host-gateway",
            "-e",
            f"GOBLIN_RUN_ID={context.run_id}",
            "-e",
            f"GOBLIN_JOB_ID={context.metadata.get('job_id', '')}",
            "-e",
            f"GOBLIN_WORKER_ID={worker_id}",
            "-e",
            "GOBLIN_INPUT_PATH=/goblin/input.json",
            "-e",
            "GOBLIN_CONTEXT_PATH=/goblin/context.json",
            "-e",
            "GOBLIN_RESULT_PATH=/goblin/result.json",
            "-e",
            "GOBLIN_ARTIFACT_ROOT=/artifacts",
            "-e",
            f"GOBLIN_REDIS_URL={_container_redis_url(self.redis_url)}",
            "-e",
            f"GOBLIN_HEARTBEAT_REDIS_URL={_container_redis_url(self.redis_url)}",
            "-e",
            f"GOBLIN_HEARTBEAT_CHANNEL={DEFAULT_HEARTBEAT_CHANNEL}",
            "-e",
            f"GOBLIN_HEARTBEAT_KEY={worker_heartbeat_key(context.run_id)}",
            "-e",
            f"GOBLIN_HEARTBEAT_INTERVAL_SECONDS={self.heartbeat_interval_seconds}",
            "-v",
            f"{run_dir}:/goblin",
            "-v",
            f"{artifact_root}:/artifacts",
        ]
        if timeout_seconds is not None:
            command.extend(["--stop-timeout", str(max(timeout_seconds, 1))])
        command.append(image)
        return command

    def _record_worker_heartbeats(self, context: GoblinContext) -> None:
        """Read heartbeat payloads left by a worker in Redis and persist them."""
        if self.event_bus is None:
            return
        try:
            client = Redis.from_url(self.redis_url)
            key = worker_heartbeat_key(context.run_id)
            for payload in client.lrange(key, 0, -1):
                self.event_bus.record_worker_heartbeat_payload(payload)
            client.expire(key, 3600)
        except RedisError as error:
            self.event_bus.emit(
                "worker.heartbeat_read_failed",
                source="runtime",
                run_id=context.run_id,
                job_id=context.metadata.get("job_id"),
                payload={"error": str(error)},
            )

    def _emit_worker_event(
        self,
        event_type: str,
        context: GoblinContext,
        worker_id: str,
        payload: dict[str, Any],
    ) -> None:
        """Emit a runtime/worker lifecycle event when an event bus is configured."""
        if self.event_bus is None:
            return
        self.event_bus.emit(
            event_type,
            source="runtime",
            project_id=context.metadata.get("project_id"),
            job_id=context.metadata.get("job_id"),
            run_id=context.run_id,
            worker_id=worker_id,
            payload=payload,
        )

    def _load_result(self, run_id: str, result_path: Path) -> GoblinResult | None:
        """Load a worker result from Redis first, then from the fallback result file."""
        result_json = None
        try:
            raw = Redis.from_url(self.redis_url).get(f"goblin-king:results:{run_id}")
            if raw is not None:
                result_json = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
        except RedisError:
            result_json = None

        if result_json is None and result_path.exists():
            result_json = result_path.read_text(encoding="utf-8")
        if result_json is None:
            return None
        try:
            return GoblinResult.model_validate_json(result_json)
        except ValueError as error:
            return GoblinResult.failed(error=f"worker produced invalid result JSON: {error}")


def _container_redis_url(redis_url: str) -> str:
    """Translate host-local Redis URLs into a Docker-container-reachable URL."""
    parsed = urlparse(redis_url)
    if parsed.hostname not in {"localhost", "127.0.0.1"}:
        return redis_url
    netloc = parsed.netloc.replace(parsed.hostname, "host.docker.internal", 1)
    return urlunparse(parsed._replace(netloc=netloc))


def new_run_context(job_id: str, kind: str, attempt: int = 1) -> GoblinContext:
    """Create a run context shared by CLI and scheduler runtime paths."""
    run_id = str(uuid4())
    return GoblinContext(
        run_id=run_id,
        artifact_root=str(Path(".goblin-king") / "artifacts" / job_id),
        metadata={
            "job_id": job_id,
            "kind": kind,
            "attempt": attempt,
            "started_at": datetime.now(UTC).isoformat(),
        },
    )
