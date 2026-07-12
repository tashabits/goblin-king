"""Configuration-path and Helm rendering tests for generated Kubernetes workloads."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from goblin_king.api_settings import ApiSettings
from goblin_king.cli import app
from goblin_king.contracts import GoblinResult
from goblin_king.kubernetes_runtime_factory import build_kubernetes_runtime
from goblin_king.kubernetes_runtime_settings import KubernetesRuntimeSettings
from goblin_king.registry import GoblinRegistry
from goblin_king.scheduler import Scheduler
from goblin_king.store import SQLiteStore
from goblin_king.workers import WorkerImageMap

runner = CliRunner()
CONTROL_DIGEST = "sha256:" + "a" * 64


def test_shared_runtime_factory_preserves_typed_settings_and_namespace(monkeypatch) -> None:
    """Verify control-plane callers share settings, namespace, and diagnostic runtime."""
    monkeypatch.setattr(
        "goblin_king.kubernetes_runtime.current_kubernetes_namespace",
        lambda: "shared-workers",
    )
    settings = KubernetesRuntimeSettings(
        result_forwarder_image=f"registry.example/control@{CONTROL_DIGEST}",
        worker_image_pull_policy="Never",
        result_forwarder_image_pull_policy="Always",
        workload_image_pull_secret_names=["registry-main"],
    )

    runtime = build_kubernetes_runtime(
        workers=WorkerImageMap.from_path("goblin-images.json"),
        redis_url="redis://redis:6379/0",
        event_bus=None,
        settings=settings,
    )

    assert runtime.settings is settings
    assert runtime.namespace == "shared-workers"
    assert runtime.result_forwarder_image.endswith(CONTROL_DIGEST)
    assert runtime.image_pull_policy == "Never"
    assert runtime.result_forwarder_image_pull_policy == "Always"
    assert runtime.workload_image_pull_secret_names == ("registry-main",)


def test_scheduler_reuses_typed_settings_for_static_and_dynamic_workers(
    tmp_path: Path,
    monkeypatch,
) -> None:
    captured: list[KubernetesRuntimeSettings] = []

    def fake_build_kubernetes_runtime(**kwargs):
        captured.append(kwargs["settings"])
        return object()

    monkeypatch.setattr(
        "goblin_king.scheduler.build_kubernetes_runtime",
        fake_build_kubernetes_runtime,
    )
    settings = KubernetesRuntimeSettings(
        result_forwarder_image=f"registry.example/control@{CONTROL_DIGEST}"
    )
    workers = WorkerImageMap.from_path("goblin-images.json")
    scheduler = Scheduler(
        registry=GoblinRegistry.from_path("examples/goblins.json"),
        store=SQLiteStore(tmp_path / "scheduler.sqlite3"),
        runtime_mode="kubernetes",
        workers=workers,
        kubernetes_runtime_settings=settings,
    )

    scheduler._build_runtime_for_workers(workers)

    assert captured == [settings, settings]


def test_scheduler_cli_forwards_kubernetes_workload_settings(tmp_path: Path, monkeypatch) -> None:
    captured = {}

    class FakeScheduler:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

        @staticmethod
        def run_once():
            return []

    monkeypatch.setattr("goblin_king.cli.Scheduler", FakeScheduler)
    settings_path = tmp_path / "kubernetes-runtime.json"
    settings_path.write_text(
        json.dumps(
            {
                "workload_security_profile": "restricted-v1",
                "restricted_workload": {
                    "worker_service_account_names": {
                        "example.echo": "goblin-echo-reader"
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        [
            "scheduler",
            "run-once",
            "--runtime",
            "kubernetes",
            "--registry",
            "examples/goblins.json",
            "--images",
            "goblin-images.json",
            "--db",
            str(tmp_path / "scheduler.sqlite3"),
            "--result-forwarder-image",
            f"registry.example/control@{CONTROL_DIGEST}",
            "--worker-image-pull-policy",
            "Never",
            "--result-forwarder-image-pull-policy",
            "Always",
            "--workload-image-pull-secret",
            "registry-main",
            "--workload-image-pull-secret",
            "registry-backup",
            "--kubernetes-runtime-settings",
            str(settings_path),
        ],
    )

    assert result.exit_code == 0, result.output
    settings = captured["kubernetes_runtime_settings"]
    assert settings.result_forwarder_image.endswith(CONTROL_DIGEST)
    assert settings.worker_image_pull_policy == "Never"
    assert settings.result_forwarder_image_pull_policy == "Always"
    assert settings.workload_image_pull_secret_names == (
        "registry-main",
        "registry-backup",
    )
    assert settings.workload_security_profile == "restricted-v1"
    assert settings.restricted_workload.worker_service_account_names == {
        "example.echo": "goblin-echo-reader"
    }


def test_direct_submit_forwards_kubernetes_workload_settings(tmp_path: Path, monkeypatch) -> None:
    captured = {}

    class FakeKubernetesRuntime:
        @staticmethod
        def run(*_args, **_kwargs):
            return GoblinResult.ok(data={"ok": True})

    def fake_build_kubernetes_runtime(**kwargs):
        captured.update(kwargs)
        return FakeKubernetesRuntime()

    monkeypatch.setattr(
        "goblin_king.cli.build_kubernetes_runtime",
        fake_build_kubernetes_runtime,
    )
    input_path = tmp_path / "input.json"
    input_path.write_text('{"message":"hello"}', encoding="utf-8")
    result = runner.invoke(
        app,
        [
            "jobs",
            "submit",
            "example.echo",
            "--input",
            str(input_path),
            "--runtime",
            "kubernetes",
            "--registry",
            "examples/goblins.json",
            "--images",
            "goblin-images.json",
            "--db",
            str(tmp_path / "jobs.sqlite3"),
            "--result-forwarder-image",
            f"registry.example/control@{CONTROL_DIGEST}",
            "--workload-image-pull-secret",
            "registry-main",
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured["settings"].result_forwarder_image.endswith(CONTROL_DIGEST)
    assert captured["settings"].workload_image_pull_secret_names == ("registry-main",)
    assert captured["settings"].workload_security_profile == "legacy"


def test_api_settings_load_kubernetes_runtime_boundary(tmp_path: Path) -> None:
    path = tmp_path / "api.json"
    path.write_text(
        json.dumps(
            {
                "kubernetes_runtime": {
                    "result_forwarder_image": f"registry.example/control@{CONTROL_DIGEST}",
                    "worker_image_pull_policy": "Never",
                    "result_forwarder_image_pull_policy": "Always",
                    "workload_image_pull_secret_names": ["registry-main"],
                    "workload_security_profile": "restricted-v1",
                    "restricted_workload": {
                        "worker_service_account_names": {
                            "example.echo": "goblin-echo-reader"
                        }
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    settings = ApiSettings.from_path(path).kubernetes_runtime

    assert settings.result_forwarder_image.endswith(CONTROL_DIGEST)
    assert settings.worker_image_pull_policy == "Never"
    assert settings.result_forwarder_image_pull_policy == "Always"
    assert settings.workload_image_pull_secret_names == ("registry-main",)
    assert settings.workload_security_profile == "restricted-v1"
    assert settings.restricted_workload.worker_service_account_names == {
        "example.echo": "goblin-echo-reader"
    }
