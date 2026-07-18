"""Tests for notebook-authored ASGI service runtime helpers."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from runpy import run_path

import pytest

from goblin_king.contracts import NotebookServiceRecord, utc_now
from goblin_king.notebook_services import (
    NotebookServiceRuntimeManager,
    notebook_service_runtime_name,
    notebook_service_source_hash,
)


def _record() -> NotebookServiceRecord:
    source = "from fastapi import FastAPI\napp = FastAPI()\n"
    return NotebookServiceRecord(
        kind="notebook.long-hello",
        display_name="Notebook Long Hello",
        image="goblin-king-notebook-asgi-service:local",
        source=source,
        source_hash=notebook_service_source_hash(source, "app", ["fastapi>=0.115,<1"]),
        app_name="app",
        requirements=["fastapi>=0.115,<1"],
        port=8080,
        probe_path="/hello",
        created_at=utc_now(),
        updated_at=utc_now(),
    )


def test_docker_runtime_command_uses_volume_network_and_no_socket(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Verify Docker service resources are launched through the shared volume/network."""
    record = _record()
    commands = []

    def fake_run(command, **_kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="container-id\n", stderr="")

    monkeypatch.setenv("GOBLIN_KING_DOCKER_NETWORK", "goblin-king_default")
    monkeypatch.setenv("GOBLIN_KING_DOCKER_DATA_VOLUME", "goblin-king_goblin-king-data")
    monkeypatch.setenv(
        "GOBLIN_KING_NOTEBOOK_SERVICE_DEPENDENCY_PROXY",
        "http://dependency-egress-proxy:8888",
    )
    monkeypatch.setattr("goblin_king.notebook_services.subprocess.run", fake_run)
    monkeypatch.setattr(
        "goblin_king.notebook_services.probe_http",
        lambda url, **_kwargs: {"ok": True, "url": url},
    )
    manager = NotebookServiceRuntimeManager(
        image="goblin-king-notebook-asgi-service:local",
        runtime="docker",
        work_root=tmp_path / "data" / "notebook-services",
    )

    proof = manager.start(record)

    run_command = commands[1]
    runtime_name = notebook_service_runtime_name(record.kind, record.source_hash)
    assert proof.backend == "docker"
    assert proof.name == runtime_name
    assert proof.base_url == f"http://{runtime_name}:8080"
    assert "--network" in run_command
    assert "goblin-king_default" in run_command
    assert "/var/run/docker.sock" not in " ".join(run_command)
    assert f"goblin-king.notebook-service-kind={record.kind}" in run_command
    assert "HTTP_PROXY=http://dependency-egress-proxy:8888" in run_command
    assert "HTTPS_PROXY=http://dependency-egress-proxy:8888" in run_command
    assert "NO_PROXY=127.0.0.1,localhost,.svc,.cluster.local" in run_command
    assert "goblin-king_goblin-king-data:/goblin-data:ro" in run_command
    source_env = next(
        item for item in run_command if item.startswith("GOBLIN_NOTEBOOK_SERVICE_SOURCE=")
    )
    assert source_env.endswith(f"notebook-services/{runtime_name}/source.py")


def test_kubernetes_runtime_manifests_are_bounded_and_hardened(monkeypatch) -> None:
    """Verify generated service Pods retain the restricted workload boundary."""
    monkeypatch.setenv(
        "GOBLIN_KING_NOTEBOOK_SERVICE_DEPENDENCY_PROXY",
        "http://dependency-egress-proxy:8888",
    )
    record = _record()
    manager = NotebookServiceRuntimeManager(
        image="registry.example/goblin-king-notebook-asgi-service:tag",
        runtime="kubernetes",
        namespace="goblin",
        image_pull_policy="Never",
    )
    name = notebook_service_runtime_name(record.kind, record.source_hash)

    deployment = manager._deployment_manifest(record, name)
    service = manager._service_manifest(record, name)

    labels = deployment["metadata"]["labels"]
    pod = deployment["spec"]["template"]["spec"]
    container = pod["containers"][0]
    assert labels["goblin-king.io/notebook-service"] == "true"
    assert labels["goblin-king.io/notebook-service-name"] == name
    assert container["image"] == "registry.example/goblin-king-notebook-asgi-service:tag"
    assert container["imagePullPolicy"] == "Never"
    assert container["ports"] == [{"containerPort": 8080}]
    assert {"name": "PORT", "value": "8080"} in container["env"]
    assert pod["automountServiceAccountToken"] is False
    assert pod["securityContext"] == {
        "runAsNonRoot": True,
        "runAsUser": 65532,
        "runAsGroup": 65532,
        "fsGroup": 65532,
        "fsGroupChangePolicy": "OnRootMismatch",
        "seccompProfile": {"type": "RuntimeDefault"},
    }
    assert container["securityContext"] == {
        "allowPrivilegeEscalation": False,
        "capabilities": {"drop": ["ALL"]},
        "privileged": False,
        "readOnlyRootFilesystem": True,
        "runAsNonRoot": True,
        "runAsUser": 65532,
        "runAsGroup": 65532,
    }
    assert container["resources"] == {
        "requests": {"cpu": "100m", "memory": "64Mi"},
        "limits": {"cpu": "1", "memory": "512Mi"},
    }
    assert container["volumeMounts"] == [
        {"name": "bundle", "mountPath": "/goblin-service", "readOnly": True},
        {"name": "runtime", "mountPath": "/tmp"},
    ]
    assert pod["volumes"] == [
        {"name": "bundle", "configMap": {"name": name}},
        {"name": "runtime", "emptyDir": {"sizeLimit": "512Mi"}},
    ]
    assert {"name": "PIP_TARGET", "value": "/tmp/goblin-service-runtime"} in container["env"]
    assert {"name": "PYTHONPATH", "value": "/tmp/goblin-service-runtime"} in container["env"]
    assert {
        "name": "HTTP_PROXY",
        "value": "http://dependency-egress-proxy:8888",
    } in container["env"]
    assert {
        "name": "HTTPS_PROXY",
        "value": "http://dependency-egress-proxy:8888",
    } in container["env"]
    assert {
        "name": "NO_PROXY",
        "value": "127.0.0.1,localhost,.svc,.cluster.local",
    } in container["env"]
    assert service["spec"]["selector"] == {"goblin-king.io/notebook-service-name": name}
    assert service["spec"]["ports"] == [{"name": "http", "port": 8080, "targetPort": 8080}]


def test_notebook_service_dependency_proxy_rejects_embedded_credentials(monkeypatch) -> None:
    """Keep dependency credentials out of generated container and Pod specifications."""
    monkeypatch.setenv(
        "GOBLIN_KING_NOTEBOOK_SERVICE_DEPENDENCY_PROXY",
        "https://operator:secret@proxy.example:8443",
    )
    manager = NotebookServiceRuntimeManager(
        image="goblin-king-notebook-asgi-service:local",
        runtime="kubernetes",
    )

    with pytest.raises(RuntimeError, match="credential-free HTTP\\(S\\) origin"):
        manager._deployment_manifest(_record(), "dependency-proxy-proof")


def test_service_runner_preserves_image_path_and_exposes_target_scripts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Keep declared dependency entry points available under a writable pip target."""
    runner = run_path(
        str(Path(__file__).parents[1] / "workers" / "notebook.asgi-service" / "runner.py")
    )
    target = tmp_path / "requirements"
    target_bin = target / "bin"
    target_bin.mkdir(parents=True)
    if os.name == "nt":
        entry_point = target_bin / "declared-cli.cmd"
        entry_point.write_text("@echo declared-console-entry\n", encoding="utf-8")
    else:
        entry_point = target_bin / "declared-cli"
        entry_point.write_text("#!/bin/sh\nprintf 'declared-console-entry\\n'\n", encoding="utf-8")
        entry_point.chmod(0o755)
    image_path = os.pathsep.join([str(Path("image") / "bin"), str(Path("usr") / "bin")])
    monkeypatch.setenv("PIP_TARGET", str(target))
    monkeypatch.setenv("PATH", image_path)

    expose = runner["_expose_pip_target_scripts"]
    expose()
    expose()

    assert os.environ["PATH"].split(os.pathsep) == [
        str(target_bin),
        str(Path("image") / "bin"),
        str(Path("usr") / "bin"),
    ]
    resolved = shutil.which("declared-cli")
    assert resolved is not None
    completed = subprocess.run(
        resolved if os.name == "nt" else [resolved],
        check=True,
        capture_output=True,
        text=True,
        shell=os.name == "nt",
    )
    assert completed.stdout.strip() == "declared-console-entry"
