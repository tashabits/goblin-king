"""Configuration-path and Helm rendering tests for generated Kubernetes workloads."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from goblin_king.api_settings import ApiSettings
from goblin_king.cli import app
from goblin_king.contracts import GoblinResult
from goblin_king.kubernetes_runtime_settings import KubernetesRuntimeSettings
from goblin_king.registry import GoblinRegistry
from goblin_king.scheduler import Scheduler
from goblin_king.store import SQLiteStore
from goblin_king.workers import WorkerImageMap

runner = CliRunner()
CONTROL_DIGEST = "sha256:" + "a" * 64


def test_scheduler_reuses_typed_settings_for_static_and_dynamic_workers(
    tmp_path: Path,
    monkeypatch,
) -> None:
    captured: list[KubernetesRuntimeSettings] = []

    class FakeKubernetesRuntime:
        def __init__(self, **kwargs) -> None:
            captured.append(kwargs["settings"])

    monkeypatch.setattr("goblin_king.scheduler.KubernetesRuntime", FakeKubernetesRuntime)
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


def test_direct_submit_forwards_kubernetes_workload_settings(tmp_path: Path, monkeypatch) -> None:
    captured = {}

    class FakeKubernetesRuntime:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

        @staticmethod
        def run(*_args, **_kwargs):
            return GoblinResult.ok(data={"ok": True})

    monkeypatch.setattr("goblin_king.cli.KubernetesRuntime", FakeKubernetesRuntime)
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
