"""Runtime adapters execute goblin definitions and normalize their result envelopes."""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from redis import Redis
from redis.exceptions import RedisError

from goblin_king.container_logs import container_log_payload, log_capture_limit
from goblin_king.contracts import GoblinContext, GoblinDefinition, GoblinResult
from goblin_king.docker_runtime_paths import (
    DockerRuntimePathError,
    relative_to_docker_data_root,
    resolve_docker_artifact_root,
    resolve_docker_run_root,
)
from goblin_king.events import (
    DEFAULT_HEARTBEAT_CHANNEL,
    DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
    EventBus,
    worker_heartbeat_key,
)
from goblin_king.kubernetes_runtime import KubernetesRuntime as KubernetesRuntime
from goblin_king.resource_policies import ResourcePolicy
from goblin_king.run_events import worker_run_event_environment
from goblin_king.runtime_helpers import (
    artifact_policy_error,
    container_redis_url,
    docker_policy_args,
    resource_policy_env,
)
from goblin_king.versions import GOBLIN_CONTAINER_CONTRACT_VERSION
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
        run_root: str | Path | None = None,
        docker_executable: str = "docker",
        event_bus: EventBus | None = None,
        heartbeat_interval_seconds: int = DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
    ) -> None:
        self.workers = workers
        self.redis_url = redis_url
        self.run_root = resolve_docker_run_root(run_root)
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
        resource_policy: ResourcePolicy | None = None,
    ) -> GoblinResult:
        """Run one Docker worker and normalize the result transported through Redis or file."""
        try:
            worker = self.workers.get(definition.kind)
        except WorkerConfigError as error:
            return GoblinResult.failed(error=str(error))

        try:
            context = context.model_copy(
                update={
                    "artifact_root": str(
                        resolve_docker_artifact_root(self.run_root, context.artifact_root)
                    )
                }
            )
            run_dir = self._prepare_run_dir(context, input_payload)
            result_path = run_dir / "result.json"
        except (DockerRuntimePathError, OSError) as error:
            return GoblinResult.failed(
                error=f"{definition.kind} Docker runtime setup failed: {error}"
            )
        worker_id = f"worker-{context.run_id}"
        try:
            command = self._docker_run_command(
                image=worker.image,
                run_dir=run_dir,
                context=context,
                worker_id=worker_id,
                timeout_seconds=timeout_seconds,
                resource_policy=resource_policy,
                worker_env=_worker_env(definition),
                secret_refs=_worker_secret_refs(definition),
            )
        except DockerRuntimePathError as error:
            return GoblinResult.failed(
                error=f"{definition.kind} Docker runtime setup failed: {error}"
            )
        self._emit_worker_event("worker.started", context, worker_id, {"kind": definition.kind})
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
        except OSError as error:
            self._emit_worker_event(
                "worker.failed",
                context,
                worker_id,
                {"kind": definition.kind, "phase": "launch", "error": str(error)},
            )
            return GoblinResult.failed(
                error=f"{definition.kind} Docker runtime launch failed: {error}"
            )
        except subprocess.TimeoutExpired as error:
            self._record_worker_heartbeats(context)
            self._emit_container_log_event(
                definition=definition,
                image=worker.image,
                context=context,
                worker_id=worker_id,
                stdout=error.stdout,
                stderr=error.stderr,
                exit_code=None,
                timed_out=True,
                resource_policy=resource_policy,
            )
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
        self._emit_container_log_event(
            definition=definition,
            image=worker.image,
            context=context,
            worker_id=worker_id,
            stdout=completed.stdout,
            stderr=completed.stderr,
            exit_code=completed.returncode,
            timed_out=False,
            resource_policy=resource_policy,
        )
        result = self._load_result(context.run_id, result_path)
        if result is not None:
            artifact_error = artifact_policy_error(
                result,
                resource_policy,
                Path(context.artifact_root),
            )
            if artifact_error is not None:
                self._emit_worker_event(
                    "worker.failed",
                    context,
                    worker_id,
                    {
                        "kind": definition.kind,
                        "phase": "artifact_policy",
                        "error": artifact_error,
                    },
                )
                return GoblinResult.failed(error=artifact_error, data=result.data)
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
        artifact_root = resolve_docker_artifact_root(self.run_root, context.artifact_root)
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
        resource_policy: ResourcePolicy | None = None,
        worker_env: dict[str, str] | None = None,
        secret_refs: list[str] | None = None,
    ) -> list[str]:
        """Compose a deterministic docker run command for a worker container."""
        artifact_root = resolve_docker_artifact_root(self.run_root, context.artifact_root)
        input_path = "/goblin/input.json"
        context_path = "/goblin/context.json"
        result_path = "/goblin/result.json"
        artifact_container_root = "/artifacts"
        data_volume = os.environ.get("GOBLIN_KING_DOCKER_DATA_VOLUME")
        data_mount = os.environ.get("GOBLIN_KING_DOCKER_DATA_MOUNT", "/goblin-data")
        command = [
            self.docker_executable,
            "run",
            "--rm",
            "--name",
            worker_id,
            "--label",
            "goblin-king.worker=true",
            "--label",
            f"goblin-king.run-id={context.run_id}",
            "--label",
            f"goblin-king.job-id={context.metadata.get('job_id', '')}",
            "--add-host",
            "host.docker.internal:host-gateway",
            "-e",
            f"GOBLIN_CONTRACT_VERSION={GOBLIN_CONTAINER_CONTRACT_VERSION}",
            "-e",
            f"GOBLIN_RUN_ID={context.run_id}",
            "-e",
            f"GOBLIN_JOB_ID={context.metadata.get('job_id', '')}",
            "-e",
            f"GOBLIN_WORKER_ID={worker_id}",
            "-e",
            f"GOBLIN_INPUT_PATH={input_path}",
            "-e",
            f"GOBLIN_CONTEXT_PATH={context_path}",
            "-e",
            f"GOBLIN_RESULT_PATH={result_path}",
            "-e",
            f"GOBLIN_ARTIFACT_ROOT={artifact_container_root}",
            "-e",
            f"GOBLIN_REDIS_URL={container_redis_url(self.redis_url)}",
            "-e",
            f"GOBLIN_HEARTBEAT_REDIS_URL={container_redis_url(self.redis_url)}",
            "-e",
            f"GOBLIN_HEARTBEAT_CHANNEL={DEFAULT_HEARTBEAT_CHANNEL}",
            "-e",
            f"GOBLIN_HEARTBEAT_KEY={worker_heartbeat_key(context.run_id)}",
            "-e",
            f"GOBLIN_HEARTBEAT_INTERVAL_SECONDS={self.heartbeat_interval_seconds}",
        ]
        for key, value in worker_run_event_environment(
            context.run_id,
            container_redis_url(self.redis_url),
        ).items():
            command.extend(["-e", f"{key}={value}"])
        command.extend(_docker_env_args(worker_env or {}, secret_refs or []))
        if resource_policy is not None:
            command.extend(
                [
                    "-e",
                    f"GOBLIN_EFFECTIVE_RESOURCE_POLICY_JSON={resource_policy_env(resource_policy)}",
                ]
            )
            command.extend(docker_policy_args(resource_policy))
        if data_volume:
            run_rel = relative_to_docker_data_root(run_dir, self.run_root, label="run directory")
            artifact_rel = relative_to_docker_data_root(
                artifact_root,
                self.run_root,
                label="artifact directory",
            )
            input_path = f"{data_mount}/{run_rel}/input.json"
            context_path = f"{data_mount}/{run_rel}/context.json"
            result_path = f"{data_mount}/{run_rel}/result.json"
            artifact_container_root = f"{data_mount}/{artifact_rel}"
            command.extend(
                [
                    "-e",
                    f"GOBLIN_INPUT_PATH={input_path}",
                    "-e",
                    f"GOBLIN_CONTEXT_PATH={context_path}",
                    "-e",
                    f"GOBLIN_RESULT_PATH={result_path}",
                    "-e",
                    f"GOBLIN_ARTIFACT_ROOT={artifact_container_root}",
                    "-v",
                    f"{data_volume}:{data_mount}",
                ]
            )
        else:
            command.extend(
                [
                    "-v",
                    f"{run_dir}:/goblin",
                    "-v",
                    f"{artifact_root}:/artifacts",
                ]
            )
        docker_network = os.environ.get("GOBLIN_KING_DOCKER_NETWORK")
        if docker_network:
            if resource_policy is None or resource_policy.network.mode is None:
                command.extend(["--network", docker_network])
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

    def _emit_container_log_event(
        self,
        *,
        definition: GoblinDefinition,
        image: str,
        context: GoblinContext,
        worker_id: str,
        stdout: str | bytes | None,
        stderr: str | bytes | None,
        exit_code: int | None,
        timed_out: bool,
        resource_policy: ResourcePolicy | None,
    ) -> None:
        """Persist a bounded copy of Docker wrapper output after a short-lived worker exits."""
        if self.event_bus is None:
            return
        payload = container_log_payload(
            kind=definition.kind,
            image=image,
            container_name=worker_id,
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            timed_out=timed_out,
            max_bytes=log_capture_limit(resource_policy),
        )
        self._emit_worker_event("worker.container_logs", context, worker_id, payload)

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


def _worker_env(definition: GoblinDefinition) -> dict[str, str]:
    """Extract safe literal environment values from project goblin metadata."""
    metadata_env = definition.metadata.get("env", {})
    if not isinstance(metadata_env, dict):
        return {}
    return {
        str(key): str(value)
        for key, value in metadata_env.items()
        if str(key) and value is not None
    }


def _worker_secret_refs(definition: GoblinDefinition) -> list[str]:
    """Extract secret environment variable names from project goblin metadata."""
    metadata_secret_refs = definition.metadata.get("secret_refs", [])
    if not isinstance(metadata_secret_refs, list):
        return []
    return [str(name) for name in metadata_secret_refs if str(name)]


def _docker_env_args(worker_env: dict[str, str], secret_refs: list[str]) -> list[str]:
    """Build Docker environment flags without placing secret values in argv."""
    args: list[str] = []
    for key in sorted(worker_env):
        args.extend(["-e", f"{key}={worker_env[key]}"])
    for key in sorted(set(secret_refs)):
        if key in os.environ:
            args.extend(["-e", key])
    return args


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
