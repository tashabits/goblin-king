"""Bounded diagnostic details captured from container runtime executions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from goblin_king.contracts import GoblinResult


@dataclass(frozen=True, slots=True)
class KubernetesRunObservation:
    """Describe one Kubernetes worker result plus bounded pod diagnostics."""

    result: GoblinResult
    job_created: bool = False
    result_received: bool = False
    result_envelope_valid: bool = False
    exit_code: int | None = None
    logs: dict[str, str] = field(default_factory=dict)

    def with_runtime_diagnostics(
        self,
        *,
        exit_code: int | None,
        logs: dict[str, str],
    ) -> KubernetesRunObservation:
        """Return a copy enriched with diagnostics captured before pod cleanup."""
        return KubernetesRunObservation(
            result=self.result,
            job_created=self.job_created,
            result_received=self.result_received,
            result_envelope_valid=self.result_envelope_valid,
            exit_code=exit_code,
            logs=logs,
        )

    def with_job_created(self) -> KubernetesRunObservation:
        """Return a copy that records successful Kubernetes Job creation."""
        return KubernetesRunObservation(
            result=self.result,
            job_created=True,
            result_received=self.result_received,
            result_envelope_valid=self.result_envelope_valid,
            exit_code=self.exit_code,
            logs=self.logs,
        )


def capture_kubernetes_pod_diagnostics(
    *,
    core: Any,
    namespace: str,
    job_name: str,
    observation: KubernetesRunObservation,
    limit_bytes: int,
) -> KubernetesRunObservation:
    """Capture bounded pod logs and a worker exit code before Job cleanup."""
    try:
        pods = core.list_namespaced_pod(
            namespace=namespace,
            label_selector=f"job-name={job_name}",
        )
        if not pods.items:
            return observation
        pod = pods.items[0]
        logs = {
            container: str(
                core.read_namespaced_pod_log(
                    name=pod.metadata.name,
                    namespace=namespace,
                    container=container,
                    limit_bytes=limit_bytes,
                )
            )
            for container in ("worker", "result-forwarder")
        }
        return observation.with_runtime_diagnostics(
            exit_code=_container_exit_code(pod, "worker"),
            logs=logs,
        )
    except Exception as error:  # pragma: no cover - diagnostic best effort
        return observation.with_runtime_diagnostics(
            exit_code=observation.exit_code,
            logs={"diagnostic-error": str(error)},
        )


def _container_exit_code(pod: Any, container_name: str) -> int | None:
    """Return one terminated container's exit code from a Kubernetes pod object."""
    statuses = getattr(getattr(pod, "status", None), "container_statuses", None) or []
    for status in statuses:
        if getattr(status, "name", None) != container_name:
            continue
        terminated = getattr(getattr(status, "state", None), "terminated", None)
        exit_code = getattr(terminated, "exit_code", None)
        return int(exit_code) if exit_code is not None else None
    return None
