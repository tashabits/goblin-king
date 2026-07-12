"""Focused tests for Kubernetes runtime configuration and startup diagnostics."""

from types import SimpleNamespace

import pytest

from goblin_king.contracts import GoblinContext
from goblin_king.kubernetes_pod_diagnostics import (
    DEFAULT_KUBERNETES_LOG_CAPTURE_BYTES,
    read_bounded_kubernetes_pod_log,
)
from goblin_king.kubernetes_runtime import KubernetesRuntime as ExtractedKubernetesRuntime
from goblin_king.kubernetes_runtime_settings import KubernetesRuntimeSettings
from goblin_king.runtime import KubernetesRuntime
from goblin_king.workers import WorkerImageDefinition, WorkerImageMap


def _runtime(*, settings: KubernetesRuntimeSettings | None = None) -> KubernetesRuntime:
    return KubernetesRuntime(
        workers=WorkerImageMap(
            {"example.echo": WorkerImageDefinition(context=".", image="echo:local")},
            root=".",
        ),
        namespace="workers",
        redis_url="redis://redis:6379/0",
        poll_interval_seconds=0,
        settings=settings,
    )


def _context() -> GoblinContext:
    return GoblinContext(
        run_id="run-images",
        artifact_root=".goblin-king/artifacts/run-images",
        metadata={"job_id": "job-images"},
    )


def test_runtime_module_keeps_kubernetes_runtime_import_compatible() -> None:
    assert KubernetesRuntime is ExtractedKubernetesRuntime


def test_legacy_pull_policy_still_applies_to_both_containers() -> None:
    runtime = KubernetesRuntime(
        workers=_runtime().workers,
        image_pull_policy="Always",
    )

    manifest = runtime._job_manifest(
        name="gk-example-echo-run-images",
        config_name="gk-example-echo-run-images-input",
        image="echo:local",
        context=_context(),
        worker_id="k8s-worker-run-images",
        timeout_seconds=30,
    )

    assert runtime.image_pull_policy == "Always"
    assert runtime.result_forwarder_image_pull_policy == "Always"
    assert [
        container["imagePullPolicy"]
        for container in manifest["spec"]["template"]["spec"]["containers"]
    ] == ["Always", "Always"]


def test_settings_deduplicate_symbolic_pull_secret_names() -> None:
    settings = KubernetesRuntimeSettings(
        workload_image_pull_secret_names=["registry-main", "registry-main", "registry-backup"]
    )

    assert settings.workload_image_pull_secret_names == (
        "registry-main",
        "registry-backup",
    )


@pytest.mark.parametrize("value", ["", "   ", ["valid", ""]])
def test_settings_reject_empty_image_or_pull_secret_names(value: object) -> None:
    field = (
        {"result_forwarder_image": value}
        if isinstance(value, str)
        else {"workload_image_pull_secret_names": value}
    )

    with pytest.raises(ValueError):
        KubernetesRuntimeSettings.model_validate(field)


@pytest.mark.parametrize(
    "payload",
    [
        {"workload_image_pull_secret_names": ["user:password"]},
        {"registry_password": "secret"},
        {"pod_spec": {"hostNetwork": True}},
    ],
)
def test_settings_reject_credentials_and_raw_pod_fields(payload: dict) -> None:
    with pytest.raises(ValueError):
        KubernetesRuntimeSettings.model_validate(payload)


def test_manifest_uses_separate_image_policies_and_workload_pull_secrets() -> None:
    forwarder_digest = "registry.example/control@sha256:" + "a" * 64
    worker_digest = "registry.example/workers/echo@sha256:" + "b" * 64
    runtime = _runtime(
        settings=KubernetesRuntimeSettings(
            result_forwarder_image=forwarder_digest,
            worker_image_pull_policy="Never",
            result_forwarder_image_pull_policy="Always",
            workload_image_pull_secret_names=["registry-main", "registry-backup"],
        )
    )

    manifest = runtime._job_manifest(
        name="gk-example-echo-run-images",
        config_name="gk-example-echo-run-images-input",
        image=worker_digest,
        context=_context(),
        worker_id="k8s-worker-run-images",
        timeout_seconds=30,
    )

    pod_spec = manifest["spec"]["template"]["spec"]
    worker, forwarder = pod_spec["containers"]
    assert worker["image"] == worker_digest
    assert worker["imagePullPolicy"] == "Never"
    assert forwarder["image"] == forwarder_digest
    assert forwarder["imagePullPolicy"] == "Always"
    assert pod_spec["imagePullSecrets"] == [
        {"name": "registry-main"},
        {"name": "registry-backup"},
    ]


def test_image_pull_failure_returns_prompt_bounded_diagnostic(monkeypatch) -> None:
    long_message = "registry unavailable " * 50

    class Batch:
        @staticmethod
        def read_namespaced_job(**_kwargs):
            return SimpleNamespace(status=SimpleNamespace(succeeded=0, failed=0))

    class Core:
        request_timeout = None

        def list_namespaced_pod(self, **kwargs):
            self.request_timeout = kwargs.get("_request_timeout")
            waiting = SimpleNamespace(reason="ImagePullBackOff", message=long_message)
            container_status = SimpleNamespace(
                name="result-forwarder",
                state=SimpleNamespace(waiting=waiting),
            )
            pod = SimpleNamespace(
                metadata=SimpleNamespace(name="gk-example-echo-pod"),
                status=SimpleNamespace(
                    init_container_statuses=None,
                    container_statuses=[container_status],
                ),
            )
            return SimpleNamespace(items=[pod])

    core = Core()
    monkeypatch.setattr(
        "goblin_king.kubernetes_runtime.time.sleep",
        lambda _seconds: pytest.fail("known pull failures must not wait for another poll"),
    )

    result = _runtime()._wait_for_result(
        batch=Batch(),
        core=core,
        name="gk-example-echo-run-images",
        run_id="run-images",
        timeout_seconds=300,
    )

    assert result.status == "failed"
    assert "result-forwarder" in (result.error or "")
    assert "ImagePullBackOff" in (result.error or "")
    assert (result.error or "").endswith("...")
    assert len(result.error or "") <= 500
    assert core.request_timeout == 5


def test_failed_job_worker_logs_share_transport_and_size_bounds() -> None:
    """Verify normal execution and validation use the same bounded log reader."""

    class Core:
        list_timeout = None
        log_timeout = None
        log_limit = None

        def list_namespaced_pod(self, **kwargs):
            self.list_timeout = kwargs.get("_request_timeout")
            pod = SimpleNamespace(metadata=SimpleNamespace(name="worker-pod"))
            return SimpleNamespace(items=[pod])

        def read_namespaced_pod_log(self, **kwargs):
            self.log_timeout = kwargs.get("_request_timeout")
            self.log_limit = kwargs.get("limit_bytes")
            return "x" * 1_000

    core = Core()

    logs = _runtime()._worker_logs(core, "worker-job")

    assert core.list_timeout == 5
    assert core.log_timeout == 5
    assert core.log_limit == DEFAULT_KUBERNETES_LOG_CAPTURE_BYTES
    assert len(logs) == 500
    assert logs.endswith("...")


def test_kubernetes_log_reader_decodes_client_byte_responses() -> None:
    """Return readable validation logs when the Kubernetes client yields bytes."""
    class LogResponse:
        data = "worker output \N{CHECK MARK}".encode()
        released = False

        def release_conn(self) -> None:
            self.released = True

    class ByteLogCore:
        def __init__(self) -> None:
            self.response = LogResponse()

        def read_namespaced_pod_log(self, **kwargs):
            assert kwargs["_preload_content"] is False
            return self.response

    core = ByteLogCore()

    assert read_bounded_kubernetes_pod_log(
        core,
        namespace="workers",
        pod_name="worker-pod",
        container="worker",
    ) == "worker output \N{CHECK MARK}"
    assert core.response.released is True
