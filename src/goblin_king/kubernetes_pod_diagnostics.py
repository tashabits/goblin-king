"""Bounded diagnostics for Kubernetes worker Pods that cannot start."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

_IMAGE_PULL_FAILURE_REASONS = frozenset(
    {
        "ErrImageNeverPull",
        "ErrImagePull",
        "ImagePullBackOff",
        "InvalidImageName",
        "RegistryUnavailable",
    }
)
_DIAGNOSTIC_REQUEST_TIMEOUT_SECONDS = 5
_MAX_MESSAGE_CHARACTERS = 400
_MAX_DIAGNOSTIC_CHARACTERS = 500


@dataclass(frozen=True, slots=True)
class KubernetesContainerStartFailure:
    """Identify one generated Pod container whose image cannot be resolved."""

    pod_name: str
    container_name: str
    reason: str
    message: str

    def describe(self, job_name: str) -> str:
        """Return a concise failure suitable for the durable run envelope."""
        detail = f": {self.message}" if self.message else ""
        description = (
            f"Kubernetes Job {job_name} Pod {self.pod_name} container "
            f"{self.container_name} could not pull its image: {self.reason}{detail}"
        )
        return _bounded_text(description, _MAX_DIAGNOSTIC_CHARACTERS)


def find_image_pull_failure(
    core: Any,
    *,
    namespace: str,
    job_name: str,
) -> KubernetesContainerStartFailure | None:
    """Inspect the generated Pod once and return a known image-pull failure, if any."""
    try:
        pods = core.list_namespaced_pod(
            namespace=namespace,
            label_selector=f"job-name={job_name}",
            _request_timeout=_DIAGNOSTIC_REQUEST_TIMEOUT_SECONDS,
        )
    except TypeError:  # lightweight clients and test doubles may omit transport options
        try:
            pods = core.list_namespaced_pod(
                namespace=namespace,
                label_selector=f"job-name={job_name}",
            )
        except Exception:
            return None
    except Exception:
        return None

    for pod in getattr(pods, "items", ()):
        pod_status = getattr(pod, "status", None)
        statuses = tuple(getattr(pod_status, "init_container_statuses", None) or ()) + tuple(
            getattr(pod_status, "container_statuses", None) or ()
        )
        for container_status in statuses:
            waiting = getattr(getattr(container_status, "state", None), "waiting", None)
            reason = str(getattr(waiting, "reason", "") or "")
            if reason not in _IMAGE_PULL_FAILURE_REASONS:
                continue
            return KubernetesContainerStartFailure(
                pod_name=str(getattr(getattr(pod, "metadata", None), "name", "unknown")),
                container_name=str(getattr(container_status, "name", "unknown")),
                reason=reason,
                message=_bounded_message(getattr(waiting, "message", "")),
            )
    return None


def _bounded_message(value: object) -> str:
    normalized = " ".join(str(value or "").split())
    return _bounded_text(normalized, _MAX_MESSAGE_CHARACTERS)


def _bounded_text(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 3] + "..."
