"""Runtime helpers for notebook-authored ASGI service workloads."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib import error as urlerror
from urllib import request as urlrequest

from goblin_king.contracts import NotebookServiceRecord
from goblin_king.runtime_helpers import current_kubernetes_namespace, kubernetes_name

RUNNER_SOURCE_PATH = "/goblin-service/source.py"
RUNNER_REQUIREMENTS_PATH = "/goblin-service/requirements.txt"
KUBERNETES_RUNTIME_PATH = "/tmp/goblin-service-runtime"


def _kubernetes_service_resources() -> dict[str, dict[str, str]]:
    """Return the fixed resource envelope for notebook-authored services."""
    return {
        "requests": {"cpu": "100m", "memory": "64Mi"},
        "limits": {"cpu": "1", "memory": "512Mi"},
    }


def _kubernetes_service_security_context() -> dict[str, object]:
    """Return the non-negotiable container security boundary."""
    return {
        "allowPrivilegeEscalation": False,
        "capabilities": {"drop": ["ALL"]},
        "privileged": False,
        "readOnlyRootFilesystem": True,
        "runAsNonRoot": True,
        "runAsUser": 65532,
        "runAsGroup": 65532,
    }


def _kubernetes_service_pod_security_context() -> dict[str, object]:
    """Return the non-root pod identity and default syscall profile."""
    return {
        "runAsNonRoot": True,
        "runAsUser": 65532,
        "runAsGroup": 65532,
        "fsGroup": 65532,
        "fsGroupChangePolicy": "OnRootMismatch",
        "seccompProfile": {"type": "RuntimeDefault"},
    }


class NotebookServiceRuntimeError(RuntimeError):
    """Raised when a notebook service runtime cannot validate, start, or stop."""


@dataclass(frozen=True)
class NotebookServiceRuntimeProof:
    """Runtime details returned after a notebook service is started or validated."""

    backend: str
    name: str
    base_url: str
    probe: dict[str, Any] | None = None

    def model(self) -> dict[str, Any]:
        """Return a JSON-serializable runtime proof payload."""
        return {
            "backend": self.backend,
            "name": self.name,
            "base_url": self.base_url,
            "probe": self.probe,
        }


def notebook_service_source_hash(
    source: str,
    app_name: str,
    requirements: list[str],
) -> str:
    """Return a stable identity for a notebook-defined ASGI service bundle."""
    payload = {
        "app_name": app_name,
        "requirements": requirements,
        "source": source,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def notebook_service_runtime_name(
    kind: str,
    source_hash: str,
    *,
    suffix: str | None = None,
) -> str:
    """Return a stable Docker/Kubernetes-safe resource name for a service bundle."""
    base = kubernetes_name(f"gk-nbsvc-{kind}-{source_hash[:12]}")
    if suffix is None:
        return base[:63].strip("-")
    suffix_part = kubernetes_name(suffix)
    max_base = 63 - len(suffix_part) - 1
    return f"{base[:max_base].strip('-')}-{suffix_part}".strip("-")


def probe_http(url: str, *, timeout_seconds: float = 60.0) -> dict[str, Any]:
    """Probe an HTTP endpoint until it returns a response or the timeout expires."""
    deadline = time.monotonic() + timeout_seconds
    last_error = "probe timed out"
    while time.monotonic() < deadline:
        try:
            with urlrequest.urlopen(url, timeout=min(5.0, timeout_seconds)) as response:
                text = response.read().decode("utf-8")
                parsed: Any
                try:
                    parsed = json.loads(text)
                except json.JSONDecodeError:
                    parsed = text
                return {
                    "ok": 200 <= response.status < 300,
                    "status_code": response.status,
                    "headers": dict(response.headers.items()),
                    "json": parsed if isinstance(parsed, dict | list) else None,
                    "text": parsed if isinstance(parsed, str) else text,
                }
        except (OSError, urlerror.URLError, urlerror.HTTPError) as error:
            last_error = str(error)
            time.sleep(1.0)
    raise NotebookServiceRuntimeError(f"service probe failed for {url}: {last_error}")


class NotebookServiceRuntimeManager:
    """Start and stop notebook-defined ASGI services in Docker or Kubernetes."""

    def __init__(
        self,
        *,
        image: str,
        runtime: str = "auto",
        work_root: Path | str = Path(".goblin-king") / "notebook-services",
        docker_executable: str = "docker",
        namespace: str | None = None,
        image_pull_policy: str = "IfNotPresent",
    ) -> None:
        self.image = image
        self.runtime = runtime
        self.work_root = Path(work_root)
        self.docker_executable = docker_executable
        self.namespace = namespace or current_kubernetes_namespace()
        self.image_pull_policy = image_pull_policy

    def validate(
        self,
        record: NotebookServiceRecord,
        *,
        timeout_seconds: float = 120.0,
    ) -> NotebookServiceRuntimeProof:
        """Start a temporary service runner, probe it, and clean it up."""
        name = notebook_service_runtime_name(record.kind, record.source_hash, suffix="validate")
        proof = self.start(record, name=name, timeout_seconds=timeout_seconds)
        try:
            return proof
        finally:
            self.stop_by_backend(proof.backend, proof.name)

    def start(
        self,
        record: NotebookServiceRecord,
        *,
        name: str | None = None,
        timeout_seconds: float = 120.0,
    ) -> NotebookServiceRuntimeProof:
        """Start a managed service and return runtime proof including a probe."""
        backend = self._resolved_backend()
        runtime_name = name or notebook_service_runtime_name(record.kind, record.source_hash)
        if backend == "kubernetes":
            return self._start_kubernetes(record, runtime_name, timeout_seconds=timeout_seconds)
        if backend == "docker":
            return self._start_docker(record, runtime_name, timeout_seconds=timeout_seconds)
        raise NotebookServiceRuntimeError(f"unsupported notebook service runtime: {backend}")

    def stop(self, record: NotebookServiceRecord) -> dict[str, Any]:
        """Stop a previously started managed service by its persisted runtime fields."""
        if not record.runtime_backend or not record.runtime_name:
            return {"backend": None, "name": None, "stopped": False, "detail": "no runtime"}
        self.stop_by_backend(record.runtime_backend, record.runtime_name)
        return {
            "backend": record.runtime_backend,
            "name": record.runtime_name,
            "stopped": True,
        }

    def stop_by_backend(self, backend: str, name: str) -> None:
        """Stop a runtime resource without needing the full service record."""
        if backend == "docker":
            subprocess.run(
                [self.docker_executable, "rm", "-f", name],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            return
        if backend == "kubernetes":
            self._delete_kubernetes_resources(name)
            return
        raise NotebookServiceRuntimeError(f"unsupported notebook service runtime: {backend}")

    def _resolved_backend(self) -> str:
        if self.runtime != "auto":
            return self.runtime
        return "kubernetes" if os.environ.get("KUBERNETES_SERVICE_HOST") else "docker"

    def _write_bundle(self, record: NotebookServiceRecord, name: str) -> Path:
        bundle_dir = (self.work_root / name).resolve()
        bundle_dir.mkdir(parents=True, exist_ok=True)
        (bundle_dir / "source.py").write_text(record.source, encoding="utf-8")
        (bundle_dir / "requirements.txt").write_text(
            "\n".join(record.requirements) + ("\n" if record.requirements else ""),
            encoding="utf-8",
        )
        return bundle_dir

    def _start_docker(
        self,
        record: NotebookServiceRecord,
        name: str,
        *,
        timeout_seconds: float,
    ) -> NotebookServiceRuntimeProof:
        bundle_dir = self._write_bundle(record, name)
        self.stop_by_backend("docker", name)
        docker_network = os.environ.get("GOBLIN_KING_DOCKER_NETWORK")
        volume_args, source_path, requirements_path = self._docker_bundle_volume_args(bundle_dir)
        command = [
            self.docker_executable,
            "run",
            "-d",
            "--name",
            name,
            "--label",
            "goblin-king.notebook-service=true",
            "--label",
            f"goblin-king.notebook-service-kind={record.kind}",
            "--label",
            f"goblin-king.notebook-service-source={record.source_hash}",
            "--add-host",
            "host.docker.internal:host-gateway",
            "-e",
            f"GOBLIN_NOTEBOOK_SERVICE_APP={record.app_name}",
            "-e",
            f"GOBLIN_NOTEBOOK_SERVICE_SOURCE={source_path}",
            "-e",
            f"GOBLIN_NOTEBOOK_SERVICE_REQUIREMENTS={requirements_path}",
            "-e",
            f"PORT={record.port}",
        ]
        if docker_network:
            command.extend(["--network", docker_network])
        else:
            command.extend(["-p", f"127.0.0.1::{record.port}"])
        command.extend(volume_args)
        command.extend([self.image, "serve"])
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if completed.returncode != 0:
            raise NotebookServiceRuntimeError(
                f"failed to start Docker notebook service {name}: {completed.stderr.strip()}"
            )
        base_url = (
            f"http://{name}:{record.port}"
            if docker_network
            else f"http://127.0.0.1:{self._docker_host_port(name, record.port)}"
        )
        probe_url = f"{base_url.rstrip('/')}{record.probe_path}"
        try:
            probe = probe_http(probe_url, timeout_seconds=timeout_seconds)
        except NotebookServiceRuntimeError as error:
            logs = self._docker_logs(name)
            self.stop_by_backend("docker", name)
            raise NotebookServiceRuntimeError(f"{error}; container logs: {logs}") from error
        return NotebookServiceRuntimeProof(
            backend="docker",
            name=name,
            base_url=base_url,
            probe=probe,
        )

    def _docker_bundle_volume_args(self, bundle_dir: Path) -> tuple[list[str], str, str]:
        data_volume = os.environ.get("GOBLIN_KING_DOCKER_DATA_VOLUME")
        data_mount = os.environ.get("GOBLIN_KING_DOCKER_DATA_MOUNT", "/goblin-data")
        if not data_volume:
            return (
                ["-v", f"{bundle_dir}:/goblin-service:ro"],
                RUNNER_SOURCE_PATH,
                RUNNER_REQUIREMENTS_PATH,
            )
        data_root = self.work_root.resolve().parent
        bundle_rel = bundle_dir.relative_to(data_root).as_posix()
        return (
            ["-v", f"{data_volume}:{data_mount}:ro"],
            f"{data_mount}/{bundle_rel}/source.py",
            f"{data_mount}/{bundle_rel}/requirements.txt",
        )

    def _docker_host_port(self, name: str, port: int) -> str:
        completed = subprocess.run(
            [self.docker_executable, "port", name, f"{port}/tcp"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if completed.returncode != 0:
            raise NotebookServiceRuntimeError(
                f"could not inspect Docker service port for {name}: {completed.stderr.strip()}"
            )
        mapping = completed.stdout.strip().splitlines()[0]
        return mapping.rsplit(":", 1)[-1]

    def _docker_logs(self, name: str) -> str:
        completed = subprocess.run(
            [self.docker_executable, "logs", name],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return (completed.stdout + completed.stderr).strip()[-4000:]

    def _start_kubernetes(
        self,
        record: NotebookServiceRecord,
        name: str,
        *,
        timeout_seconds: float,
    ) -> NotebookServiceRuntimeProof:
        from kubernetes import client, config

        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config()
        core = client.CoreV1Api()
        apps = client.AppsV1Api()
        try:
            self._delete_kubernetes_resources(name)
            core.create_namespaced_config_map(
                namespace=self.namespace,
                body={
                    "apiVersion": "v1",
                    "kind": "ConfigMap",
                    "metadata": {
                        "name": name,
                        "labels": self._kubernetes_labels(record, name),
                    },
                    "data": {
                        "source.py": record.source,
                        "requirements.txt": "\n".join(record.requirements),
                    },
                },
            )
            apps.create_namespaced_deployment(
                namespace=self.namespace,
                body=self._deployment_manifest(record, name),
            )
            core.create_namespaced_service(
                namespace=self.namespace,
                body=self._service_manifest(record, name),
            )
            self._wait_for_kubernetes_deployment(apps, name, timeout_seconds=timeout_seconds)
            base_url = f"http://{name}.{self.namespace}.svc.cluster.local:{record.port}"
            probe_url = f"{base_url.rstrip('/')}{record.probe_path}"
            probe = probe_http(probe_url, timeout_seconds=timeout_seconds)
        except Exception as error:
            logs = self._kubernetes_logs(core, name)
            self._delete_kubernetes_resources(name)
            if isinstance(error, NotebookServiceRuntimeError):
                raise NotebookServiceRuntimeError(f"{error}; pod logs: {logs}") from error
            raise NotebookServiceRuntimeError(
                f"kubernetes notebook service start failed: {error}; pod logs: {logs}"
            ) from error
        return NotebookServiceRuntimeProof(
            backend="kubernetes",
            name=name,
            base_url=base_url,
            probe=probe,
        )

    def _deployment_manifest(self, record: NotebookServiceRecord, name: str) -> dict[str, Any]:
        labels = self._kubernetes_labels(record, name)
        return {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {"name": name, "labels": labels},
            "spec": {
                "replicas": 1,
                "selector": {"matchLabels": {"goblin-king.io/notebook-service-name": name}},
                "template": {
                    "metadata": {"labels": labels},
                    "spec": {
                        "automountServiceAccountToken": False,
                        "securityContext": _kubernetes_service_pod_security_context(),
                        "containers": [
                            {
                                "name": "service",
                                "image": self.image,
                                "imagePullPolicy": self.image_pull_policy,
                                "ports": [{"containerPort": record.port}],
                                "env": [
                                    {
                                        "name": "GOBLIN_NOTEBOOK_SERVICE_APP",
                                        "value": record.app_name,
                                    },
                                    {
                                        "name": "GOBLIN_NOTEBOOK_SERVICE_SOURCE",
                                        "value": RUNNER_SOURCE_PATH,
                                    },
                                    {
                                        "name": "GOBLIN_NOTEBOOK_SERVICE_REQUIREMENTS",
                                        "value": RUNNER_REQUIREMENTS_PATH,
                                    },
                                    {"name": "PORT", "value": str(record.port)},
                                    {"name": "HOME", "value": "/tmp"},
                                    {
                                        "name": "PIP_TARGET",
                                        "value": KUBERNETES_RUNTIME_PATH,
                                    },
                                    {
                                        "name": "PYTHONPATH",
                                        "value": KUBERNETES_RUNTIME_PATH,
                                    },
                                    {"name": "PYTHONDONTWRITEBYTECODE", "value": "1"},
                                ],
                                "resources": _kubernetes_service_resources(),
                                "securityContext": (_kubernetes_service_security_context()),
                                "volumeMounts": [
                                    {
                                        "name": "bundle",
                                        "mountPath": "/goblin-service",
                                        "readOnly": True,
                                    },
                                    {"name": "runtime", "mountPath": "/tmp"},
                                ],
                            }
                        ],
                        "volumes": [
                            {"name": "bundle", "configMap": {"name": name}},
                            {
                                "name": "runtime",
                                "emptyDir": {"sizeLimit": "512Mi"},
                            },
                        ],
                    },
                },
            },
        }

    def _service_manifest(self, record: NotebookServiceRecord, name: str) -> dict[str, Any]:
        return {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {"name": name, "labels": self._kubernetes_labels(record, name)},
            "spec": {
                "selector": {"goblin-king.io/notebook-service-name": name},
                "ports": [{"name": "http", "port": record.port, "targetPort": record.port}],
            },
        }

    def _kubernetes_labels(self, record: NotebookServiceRecord, name: str) -> dict[str, str]:
        return {
            "app.kubernetes.io/name": "goblin-king",
            "app.kubernetes.io/component": "notebook-service",
            "goblin-king.io/notebook-service": "true",
            "goblin-king.io/notebook-service-name": name,
            "goblin-king.io/source-hash": record.source_hash[:32],
        }

    def _wait_for_kubernetes_deployment(
        self,
        apps: Any,
        name: str,
        *,
        timeout_seconds: float,
    ) -> None:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            deployment = apps.read_namespaced_deployment_status(
                name=name,
                namespace=self.namespace,
            )
            if (deployment.status.available_replicas or 0) >= 1:
                return
            time.sleep(1.0)
        raise NotebookServiceRuntimeError(
            f"kubernetes notebook service deployment did not become available: {name}"
        )

    def _delete_kubernetes_resources(self, name: str) -> None:
        from kubernetes import client, config
        from kubernetes.client.exceptions import ApiException

        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config()
        core = client.CoreV1Api()
        apps = client.AppsV1Api()
        for delete in (
            lambda: apps.delete_namespaced_deployment(name=name, namespace=self.namespace),
            lambda: core.delete_namespaced_service(name=name, namespace=self.namespace),
            lambda: core.delete_namespaced_config_map(name=name, namespace=self.namespace),
        ):
            try:
                delete()
            except ApiException as error:
                if error.status != 404:
                    raise

    def _kubernetes_logs(self, core: Any, name: str) -> str:
        try:
            pods = core.list_namespaced_pod(
                namespace=self.namespace,
                label_selector=f"goblin-king.io/notebook-service-name={name}",
            )
            if not pods.items:
                return ""
            return core.read_namespaced_pod_log(
                name=pods.items[0].metadata.name,
                namespace=self.namespace,
                tail_lines=80,
            )
        except Exception as error:
            return f"could not read pod logs: {error}"
