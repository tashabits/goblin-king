"""Kubernetes Job runtime for finite, contract-compliant goblin tasks."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Sequence
from typing import Any

from redis import Redis
from redis.exceptions import RedisError

from goblin_king.contracts import GoblinContext, GoblinDefinition, GoblinResult
from goblin_king.events import (
    DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
    EventBus,
    worker_heartbeat_key,
)
from goblin_king.kubernetes_job_manifest import build_kubernetes_job_manifest
from goblin_king.kubernetes_placement import placement_metadata
from goblin_king.kubernetes_pod_diagnostics import find_image_pull_failure
from goblin_king.kubernetes_runtime_settings import (
    DEFAULT_KUBERNETES_IMAGE_PULL_POLICY,
    DEFAULT_RESULT_FORWARDER_IMAGE,
    KubernetesRuntimeSettings,
)
from goblin_king.resource_policies import ResourcePolicy
from goblin_king.runtime_helpers import (
    current_kubernetes_namespace,
    kubernetes_clients,
    kubernetes_name,
)
from goblin_king.workers import WorkerConfigError, WorkerImageMap


class KubernetesRuntime:
    """Execute goblins as short-lived Kubernetes Jobs and collect Redis results."""

    def __init__(
        self,
        *,
        workers: WorkerImageMap,
        redis_url: str = "redis://localhost:6379/0",
        namespace: str | None = None,
        image_pull_policy: str = DEFAULT_KUBERNETES_IMAGE_PULL_POLICY,
        result_forwarder_image: str = DEFAULT_RESULT_FORWARDER_IMAGE,
        event_bus: EventBus | None = None,
        heartbeat_interval_seconds: int = DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
        poll_interval_seconds: float = 1.0,
        settings: KubernetesRuntimeSettings | None = None,
        result_forwarder_image_pull_policy: str | None = None,
        workload_image_pull_secret_names: Sequence[str] = (),
    ) -> None:
        self.workers = workers
        self.redis_url = redis_url
        self.namespace = namespace or current_kubernetes_namespace()
        self.settings = settings or KubernetesRuntimeSettings.from_legacy_options(
            result_forwarder_image=result_forwarder_image,
            image_pull_policy=image_pull_policy,
            result_forwarder_image_pull_policy=result_forwarder_image_pull_policy,
            workload_image_pull_secret_names=workload_image_pull_secret_names,
        )
        # Keep the established attributes available to callers that inspect the adapter.
        self.image_pull_policy = self.settings.worker_image_pull_policy
        self.result_forwarder_image = self.settings.result_forwarder_image
        self.result_forwarder_image_pull_policy = (
            self.settings.result_forwarder_image_pull_policy
        )
        self.workload_image_pull_secret_names = (
            self.settings.workload_image_pull_secret_names
        )
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
                    placement=placement_metadata(definition, context),
                    kind=definition.kind,
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
        placement: dict[str, dict[str, str]] | None = None,
        kind: str | None = None,
    ) -> dict[str, Any]:
        """Build the Kubernetes Job manifest that mirrors the Docker worker contract."""
        return build_kubernetes_job_manifest(
            name=name,
            config_name=config_name,
            image=image,
            context=context,
            worker_id=worker_id,
            timeout_seconds=timeout_seconds,
            settings=self.settings,
            redis_url=self.redis_url,
            heartbeat_interval_seconds=self.heartbeat_interval_seconds,
            resource_policy=resource_policy,
            placement=placement,
            kind=kind,
        )

    def _wait_for_result(
        self,
        *,
        batch: Any,
        core: Any,
        name: str,
        run_id: str,
        timeout_seconds: int | None,
    ) -> GoblinResult:
        """Wait for Job completion while failing promptly on a known image-pull error."""
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
            pull_failure = find_image_pull_failure(
                core,
                namespace=self.namespace,
                job_name=name,
            )
            if pull_failure is not None:
                return GoblinResult.failed(error=pull_failure.describe(name))
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
                    container="worker",
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
