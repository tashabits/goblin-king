"""Bounded diagnostics for Kubernetes worker Pods that cannot start."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from goblin_king.container_logs import tail_text
from goblin_king.contracts import GoblinResult

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
DEFAULT_KUBERNETES_LOG_CAPTURE_BYTES = 64 * 1024


@dataclass(frozen=True, slots=True)
class KubernetesRunObservation:
    """Describe one Kubernetes worker result plus bounded pod diagnostics."""

    result: GoblinResult
    job_created: bool = False
    result_received: bool = False
    result_envelope_valid: bool = False
    exit_code: int | None = None
    logs: dict[str, str] = field(default_factory=dict)
    log_truncated: dict[str, bool] = field(default_factory=dict)
    log_observed_bytes: dict[str, int] = field(default_factory=dict)
    log_read_failed: dict[str, bool] = field(default_factory=dict)

    def with_job_created(self) -> KubernetesRunObservation:
        """Return a copy that records successful Kubernetes Job creation."""
        return KubernetesRunObservation(
            result=self.result,
            job_created=True,
            result_received=self.result_received,
            result_envelope_valid=self.result_envelope_valid,
            exit_code=self.exit_code,
            logs=self.logs,
            log_truncated=self.log_truncated,
            log_observed_bytes=self.log_observed_bytes,
            log_read_failed=self.log_read_failed,
        )

    def with_runtime_diagnostics(
        self,
        *,
        exit_code: int | None,
        logs: dict[str, str],
        log_truncated: dict[str, bool] | None = None,
        log_observed_bytes: dict[str, int] | None = None,
        log_read_failed: dict[str, bool] | None = None,
    ) -> KubernetesRunObservation:
        """Return a copy enriched before the transient Job is deleted."""
        return KubernetesRunObservation(
            result=self.result,
            job_created=self.job_created,
            result_received=self.result_received,
            result_envelope_valid=self.result_envelope_valid,
            exit_code=exit_code,
            logs=logs,
            log_truncated=log_truncated or {},
            log_observed_bytes=log_observed_bytes or {},
            log_read_failed=log_read_failed or {},
        )


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
    pods = _list_job_pods(core, namespace=namespace, job_name=job_name)
    if pods is None:
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


def capture_kubernetes_pod_diagnostics(
    *,
    core: Any,
    namespace: str,
    job_name: str,
    observation: KubernetesRunObservation,
    worker_log_max_bytes: int | None = None,
) -> KubernetesRunObservation:
    """Capture bounded logs and the worker exit code before deleting a Job."""
    pods = _list_job_pods(core, namespace=namespace, job_name=job_name)
    if pods is None or not getattr(pods, "items", ()):
        return observation
    pod = pods.items[0]
    worker_limit = min(
        DEFAULT_KUBERNETES_LOG_CAPTURE_BYTES,
        (
            DEFAULT_KUBERNETES_LOG_CAPTURE_BYTES
            if worker_log_max_bytes is None
            else max(worker_log_max_bytes, 0)
        ),
    )
    pod_name = str(getattr(getattr(pod, "metadata", None), "name", "unknown"))
    worker_capture = _read_kubernetes_pod_log_capture(
        core,
        namespace=namespace,
        pod_name=pod_name,
        container="worker",
        max_bytes=worker_limit,
    )
    forwarder_log = read_bounded_kubernetes_pod_log(
        core,
        namespace=namespace,
        pod_name=pod_name,
        container="result-forwarder",
    )
    captures = {
        "worker": worker_capture,
        "result-forwarder": (
            forwarder_log,
            False,
            len(forwarder_log.encode("utf-8")),
            False,
        ),
    }
    return observation.with_runtime_diagnostics(
        exit_code=_container_exit_code(pod, "worker"),
        logs={container: capture[0] for container, capture in captures.items()},
        log_truncated={container: capture[1] for container, capture in captures.items()},
        log_observed_bytes={container: capture[2] for container, capture in captures.items()},
        log_read_failed={container: capture[3] for container, capture in captures.items()},
    )


def read_kubernetes_worker_log_excerpt(
    core: Any,
    *,
    namespace: str,
    job_name: str,
) -> str:
    """Return a compact, transport-bounded worker log excerpt for failed Jobs."""
    pods = _list_job_pods(core, namespace=namespace, job_name=job_name)
    if pods is None or not getattr(pods, "items", ()):
        return "no worker pod found"
    pod_name = str(getattr(getattr(pods.items[0], "metadata", None), "name", "unknown"))
    logs = read_bounded_kubernetes_pod_log(
        core,
        namespace=namespace,
        pod_name=pod_name,
        container="worker",
    )
    return _bounded_text(logs, _MAX_DIAGNOSTIC_CHARACTERS)


def _list_job_pods(core: Any, *, namespace: str, job_name: str) -> Any | None:
    try:
        return core.list_namespaced_pod(
            namespace=namespace,
            label_selector=f"job-name={job_name}",
            _request_timeout=_DIAGNOSTIC_REQUEST_TIMEOUT_SECONDS,
        )
    except TypeError:  # lightweight clients and test doubles may omit transport options
        try:
            return core.list_namespaced_pod(
                namespace=namespace,
                label_selector=f"job-name={job_name}",
            )
        except Exception:
            return None
    except Exception:
        return None


def read_bounded_kubernetes_pod_log(
    core: Any,
    *,
    namespace: str,
    pod_name: str,
    container: str,
) -> str:
    return _read_kubernetes_pod_log_value(
        core,
        namespace=namespace,
        pod_name=pod_name,
        container=container,
        limit_bytes=DEFAULT_KUBERNETES_LOG_CAPTURE_BYTES,
    )


def _read_kubernetes_pod_log_capture(
    core: Any,
    *,
    namespace: str,
    pod_name: str,
    container: str,
    max_bytes: int,
) -> tuple[str, bool, int, bool]:
    try:
        value = _read_kubernetes_pod_log_value(
            core,
            namespace=namespace,
            pod_name=pod_name,
            container=container,
            limit_bytes=max_bytes + 1,
            raise_on_error=True,
        )
    except Exception as error:  # diagnostic observation may retain a bounded failure reason
        message = _bounded_text(
            f"unable to read {container} logs: {error}",
            _MAX_DIAGNOSTIC_CHARACTERS,
        )
        return message, False, len(message.encode("utf-8")), True
    text, truncated, observed_bytes = tail_text(value, max_bytes)
    return text, truncated, observed_bytes, False


def _read_kubernetes_pod_log_value(
    core: Any,
    *,
    namespace: str,
    pod_name: str,
    container: str,
    limit_bytes: int,
    raise_on_error: bool = False,
) -> str:
    kwargs = {
        "name": pod_name,
        "namespace": namespace,
        "container": container,
        "limit_bytes": limit_bytes,
    }
    try:
        try:
            value = core.read_namespaced_pod_log(
                **kwargs,
                _preload_content=False,
                _request_timeout=_DIAGNOSTIC_REQUEST_TIMEOUT_SECONDS,
            )
        except TypeError:
            try:
                value = core.read_namespaced_pod_log(
                    **kwargs,
                    _request_timeout=_DIAGNOSTIC_REQUEST_TIMEOUT_SECONDS,
                )
            except TypeError:
                value = core.read_namespaced_pod_log(**kwargs)
        response = value
        response_data = getattr(response, "data", None)
        if response_data is not None:
            value = response_data
            release = getattr(response, "release_conn", None)
            if callable(release):
                release()
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return str(value)
    except Exception as error:  # diagnostic best effort
        if raise_on_error:
            raise
        return _bounded_text(
            f"unable to read {container} logs: {error}",
            _MAX_DIAGNOSTIC_CHARACTERS,
        )


def _container_exit_code(pod: Any, container_name: str) -> int | None:
    statuses = getattr(getattr(pod, "status", None), "container_statuses", None) or []
    for status in statuses:
        if getattr(status, "name", None) != container_name:
            continue
        terminated = getattr(getattr(status, "state", None), "terminated", None)
        exit_code = getattr(terminated, "exit_code", None)
        return int(exit_code) if exit_code is not None else None
    return None


def _bounded_message(value: object) -> str:
    normalized = " ".join(str(value or "").split())
    return _bounded_text(normalized, _MAX_MESSAGE_CHARACTERS)


def _bounded_text(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 3] + "..."
