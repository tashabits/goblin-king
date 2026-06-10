"""Runtime adapters execute goblin definitions and normalize their result envelopes."""

from __future__ import annotations

import json
import os
import subprocess
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
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
from goblin_king.resource_policies import ResourcePolicy
from goblin_king.runtime_helpers import (
    artifact_policy_error,
    container_redis_url,
    current_kubernetes_namespace,
    docker_policy_args,
    kubernetes_clients,
    kubernetes_name,
    kubernetes_policy_fields,
    resource_policy_env,
)
from goblin_king.versions import GOBLIN_CONTAINER_CONTRACT_VERSION
from goblin_king.workers import WorkerConfigError, WorkerImageMap

_KUBERNETES_RESULT_FORWARDER_SCRIPT = r"""
import os
import time
from pathlib import Path

from redis import Redis

run_id = os.environ["GOBLIN_RUN_ID"]
redis_url = os.environ["GOBLIN_REDIS_URL"]
result_path = Path(os.environ["GOBLIN_RESULT_PATH"])
wait_seconds = int(os.environ.get("GOBLIN_RESULT_WAIT_SECONDS", "300"))
deadline = time.monotonic() + wait_seconds

while time.monotonic() < deadline:
    if result_path.is_file():
        Redis.from_url(redis_url).set(
            f"goblin-king:results:{run_id}",
            result_path.read_text(encoding="utf-8"),
            ex=3600,
        )
        raise SystemExit(0)
    time.sleep(0.25)

raise SystemExit(f"result file not found before timeout: {result_path}")
"""


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
        resource_policy: ResourcePolicy | None = None,
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
            resource_policy=resource_policy,
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
            artifact_error = artifact_policy_error(
                result,
                resource_policy,
                Path(context.artifact_root),
            )
            if artifact_error is not None:
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
        resource_policy: ResourcePolicy | None = None,
    ) -> list[str]:
        """Compose a deterministic docker run command for a worker container."""
        artifact_root = Path(context.artifact_root)
        if not artifact_root.is_absolute():
            artifact_root = (Path.cwd() / artifact_root).resolve()
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
        if resource_policy is not None:
            command.extend(
                [
                    "-e",
                    f"GOBLIN_EFFECTIVE_RESOURCE_POLICY_JSON={resource_policy_env(resource_policy)}",
                ]
            )
            command.extend(docker_policy_args(resource_policy))
        if data_volume:
            data_root = self.run_root.resolve().parent
            run_rel = run_dir.relative_to(data_root).as_posix()
            artifact_rel = artifact_root.relative_to(data_root).as_posix()
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


class KubernetesRuntime:
    """Execute goblins as short-lived Kubernetes Jobs and collect Redis results."""

    def __init__(
        self,
        *,
        workers: WorkerImageMap,
        redis_url: str = "redis://localhost:6379/0",
        namespace: str | None = None,
        image_pull_policy: str = "IfNotPresent",
        result_forwarder_image: str = "goblin-king:local",
        event_bus: EventBus | None = None,
        heartbeat_interval_seconds: int = DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
        poll_interval_seconds: float = 1.0,
    ) -> None:
        self.workers = workers
        self.redis_url = redis_url
        self.namespace = namespace or current_kubernetes_namespace()
        self.image_pull_policy = image_pull_policy
        self.result_forwarder_image = result_forwarder_image
        self.event_bus = event_bus
        self.heartbeat_interval_seconds = heartbeat_interval_seconds
        self.poll_interval_seconds = poll_interval_seconds

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
        """Create a Kubernetes Job for one worker and return its result envelope."""
        try:
            worker = self.workers.get(definition.kind)
        except WorkerConfigError as error:
            return GoblinResult.failed(error=str(error))

        try:
            batch, core = kubernetes_clients()
        except Exception as error:  # pragma: no cover - depends on cluster config
            return GoblinResult.failed(error=f"kubernetes runtime unavailable: {error}")

        name = kubernetes_name(f"gk-{definition.kind}-{context.run_id}")
        config_name = f"{name}-input"
        worker_id = f"k8s-worker-{context.run_id}"
        self._emit_worker_event("worker.started", context, worker_id, {"kind": definition.kind})
        try:
            core.create_namespaced_config_map(
                namespace=self.namespace,
                body={
                    "apiVersion": "v1",
                    "kind": "ConfigMap",
                    "metadata": {"name": config_name},
                    "data": {
                        "input.json": json.dumps(input_payload),
                        "context.json": context.model_dump_json(),
                    },
                },
            )
            batch.create_namespaced_job(
                namespace=self.namespace,
                body=self._job_manifest(
                    name=name,
                    config_name=config_name,
                    image=worker.image,
                    context=context,
                    worker_id=worker_id,
                    timeout_seconds=timeout_seconds,
                    resource_policy=resource_policy,
                ),
            )
            result = self._wait_for_result(
                batch=batch,
                core=core,
                name=name,
                run_id=context.run_id,
                timeout_seconds=timeout_seconds,
            )
        except Exception as error:  # pragma: no cover - cluster errors vary by provider
            result = GoblinResult.failed(
                error=f"{definition.kind} Kubernetes worker failed: {error}"
            )
        finally:
            self._record_worker_heartbeats(context)
            self._cleanup(batch=batch, core=core, job_name=name, config_name=config_name)

        self._emit_worker_event(
            "worker.completed" if result.status == "success" else "worker.failed",
            context,
            worker_id,
            {"kind": definition.kind, "status": result.status, "error": result.error},
        )
        return result

    def _job_manifest(
        self,
        *,
        name: str,
        config_name: str,
        image: str,
        context: GoblinContext,
        worker_id: str,
        timeout_seconds: int | None,
        resource_policy: ResourcePolicy | None = None,
    ) -> dict[str, Any]:
        """Build the Kubernetes Job manifest that mirrors the Docker worker contract."""
        worker_container: dict[str, Any] = {
            "name": "worker",
            "image": image,
            "imagePullPolicy": self.image_pull_policy,
            "env": [
                {
                    "name": "GOBLIN_CONTRACT_VERSION",
                    "value": GOBLIN_CONTAINER_CONTRACT_VERSION,
                },
                {"name": "GOBLIN_RUN_ID", "value": context.run_id},
                {
                    "name": "GOBLIN_JOB_ID",
                    "value": str(context.metadata.get("job_id", "")),
                },
                {"name": "GOBLIN_WORKER_ID", "value": worker_id},
                {"name": "GOBLIN_INPUT_PATH", "value": "/goblin-config/input.json"},
                {
                    "name": "GOBLIN_CONTEXT_PATH",
                    "value": "/goblin-config/context.json",
                },
                {
                    "name": "GOBLIN_RESULT_PATH",
                    "value": "/goblin-result/result.json",
                },
                {"name": "GOBLIN_ARTIFACT_ROOT", "value": "/artifacts"},
                {"name": "GOBLIN_REDIS_URL", "value": self.redis_url},
                {"name": "GOBLIN_HEARTBEAT_REDIS_URL", "value": self.redis_url},
                {
                    "name": "GOBLIN_HEARTBEAT_CHANNEL",
                    "value": DEFAULT_HEARTBEAT_CHANNEL,
                },
                {
                    "name": "GOBLIN_HEARTBEAT_KEY",
                    "value": worker_heartbeat_key(context.run_id),
                },
                {
                    "name": "GOBLIN_HEARTBEAT_INTERVAL_SECONDS",
                    "value": str(self.heartbeat_interval_seconds),
                },
            ],
            "volumeMounts": [
                {"name": "input", "mountPath": "/goblin-config", "readOnly": True},
                {"name": "result", "mountPath": "/goblin-result"},
                {"name": "artifacts", "mountPath": "/artifacts"},
            ],
        }
        if resource_policy is not None:
            worker_container["env"].append(
                {
                    "name": "GOBLIN_EFFECTIVE_RESOURCE_POLICY_JSON",
                    "value": resource_policy_env(resource_policy),
                }
            )
            worker_container.update(kubernetes_policy_fields(resource_policy))
        spec: dict[str, Any] = {
            "backoffLimit": 0,
            "template": {
                "metadata": {
                    "labels": {
                        "goblin-king.worker": "true",
                        "goblin-king.run-id": context.run_id,
                        "goblin-king.job-id": str(context.metadata.get("job_id", "")),
                    }
                },
                "spec": {
                    "restartPolicy": "Never",
                    "containers": [
                        worker_container,
                        self._result_forwarder_container(
                            context=context,
                            timeout_seconds=timeout_seconds,
                        ),
                    ],
                    "volumes": [
                        {"name": "input", "configMap": {"name": config_name}},
                        {"name": "result", "emptyDir": {}},
                        {"name": "artifacts", "emptyDir": {}},
                    ],
                },
            },
        }
        if timeout_seconds is not None:
            spec["activeDeadlineSeconds"] = timeout_seconds
        return {
            "apiVersion": "batch/v1",
            "kind": "Job",
            "metadata": {
                "name": name,
                "labels": {
                    "goblin-king.worker": "true",
                    "goblin-king.run-id": context.run_id,
                    "goblin-king.job-id": str(context.metadata.get("job_id", "")),
                },
            },
            "spec": spec,
        }

    def _result_forwarder_container(
        self,
        *,
        context: GoblinContext,
        timeout_seconds: int | None,
    ) -> dict[str, Any]:
        """Publish the worker result file to Redis for language-neutral Kubernetes jobs."""
        wait_seconds = str((timeout_seconds or 300) + 15)
        return {
            "name": "result-forwarder",
            "image": self.result_forwarder_image,
            "imagePullPolicy": self.image_pull_policy,
            "command": ["python", "-c", _KUBERNETES_RESULT_FORWARDER_SCRIPT],
            "env": [
                {"name": "GOBLIN_RUN_ID", "value": context.run_id},
                {"name": "GOBLIN_REDIS_URL", "value": self.redis_url},
                {"name": "GOBLIN_RESULT_PATH", "value": "/goblin-result/result.json"},
                {"name": "GOBLIN_RESULT_WAIT_SECONDS", "value": wait_seconds},
            ],
            "volumeMounts": [
                {"name": "result", "mountPath": "/goblin-result"},
            ],
        }

    def _wait_for_result(
        self,
        *,
        batch: Any,
        core: Any,
        name: str,
        run_id: str,
        timeout_seconds: int | None,
    ) -> GoblinResult:
        """Wait for a Kubernetes Job to finish and load its Redis result."""
        started = time.monotonic()
        limit = (timeout_seconds or 300) + 30
        while time.monotonic() - started < limit:
            job = batch.read_namespaced_job(name=name, namespace=self.namespace)
            if getattr(job.status, "succeeded", 0):
                result = self._load_result(run_id)
                return result or GoblinResult.failed(
                    error=f"{name} completed without a Redis result"
                )
            if getattr(job.status, "failed", 0):
                logs = self._worker_logs(core, name)
                return GoblinResult.failed(error=f"{name} failed: {logs}")
            time.sleep(self.poll_interval_seconds)
        return GoblinResult.failed(error=f"{name} exceeded wait timeout")

    def _worker_logs(self, core: Any, job_name: str) -> str:
        """Return a compact log excerpt for the worker pod behind a Job."""
        try:
            pods = core.list_namespaced_pod(
                namespace=self.namespace,
                label_selector=f"job-name={job_name}",
            )
            if not pods.items:
                return "no worker pod found"
            return str(
                core.read_namespaced_pod_log(
                    name=pods.items[0].metadata.name,
                    namespace=self.namespace,
                    tail_lines=40,
                )
            )
        except Exception as error:  # pragma: no cover - diagnostic best effort
            return f"unable to read worker logs: {error}"

    def _load_result(self, run_id: str) -> GoblinResult | None:
        """Load a Kubernetes worker result from Redis."""
        try:
            raw = Redis.from_url(self.redis_url).get(f"goblin-king:results:{run_id}")
        except RedisError:
            return None
        if raw is None:
            return None
        result_json = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
        try:
            return GoblinResult.model_validate_json(result_json)
        except ValueError as error:
            return GoblinResult.failed(error=f"worker produced invalid result JSON: {error}")

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

    def _cleanup(self, *, batch: Any, core: Any, job_name: str, config_name: str) -> None:
        """Best-effort cleanup for transient Kubernetes runtime objects."""
        try:
            batch.delete_namespaced_job(
                name=job_name,
                namespace=self.namespace,
                propagation_policy="Background",
            )
        except Exception:
            pass
        try:
            core.delete_namespaced_config_map(name=config_name, namespace=self.namespace)
        except Exception:
            pass

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
