"""Scoped hard-termination helpers for Goblin King-owned runtime objects."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from typing import Literal

RuntimeTarget = Literal["docker", "kubernetes", "both"]


@dataclass
class TerminationResult:
    """Result of a scoped hard runtime termination attempt."""

    requested: RuntimeTarget
    killed: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Return whether the attempt completed without runtime errors."""
        return not self.errors


def terminate_runtime(
    *,
    job_id: str | None = None,
    run_id: str | None = None,
    runtime: RuntimeTarget = "both",
    namespace: str | None = None,
    docker_executable: str = "docker",
) -> TerminationResult:
    """Terminate only Docker/Kubernetes objects carrying Goblin King labels."""
    result = TerminationResult(requested=runtime)
    if runtime in {"docker", "both"}:
        _terminate_docker(
            result,
            job_id=job_id,
            run_id=run_id,
            docker_executable=docker_executable,
        )
    if runtime in {"kubernetes", "both"}:
        _terminate_kubernetes(result, job_id=job_id, run_id=run_id, namespace=namespace)
    return result


def _label_filters(job_id: str | None, run_id: str | None) -> list[str]:
    """Build label selectors that can only match Goblin King-owned objects."""
    filters = ["goblin-king.worker=true"]
    if job_id:
        filters.append(f"goblin-king.job-id={job_id}")
    if run_id:
        filters.append(f"goblin-king.run-id={run_id}")
    return filters


def _terminate_docker(
    result: TerminationResult,
    *,
    job_id: str | None,
    run_id: str | None,
    docker_executable: str,
) -> None:
    """Kill Docker containers matching Goblin King labels."""
    command = [docker_executable, "ps", "-q"]
    for label in _label_filters(job_id, run_id):
        command.extend(["--filter", f"label={label}"])
    listed = subprocess.run(command, check=False, capture_output=True, text=True)
    if listed.returncode != 0:
        result.errors.append(listed.stderr.strip() or "docker ps failed")
        return
    container_ids = [line.strip() for line in listed.stdout.splitlines() if line.strip()]
    for container_id in container_ids:
        killed = subprocess.run(
            [docker_executable, "kill", container_id],
            check=False,
            capture_output=True,
            text=True,
        )
        if killed.returncode == 0:
            result.killed.append(f"docker:{container_id}")
        else:
            result.errors.append(killed.stderr.strip() or f"docker kill failed: {container_id}")


def _terminate_kubernetes(
    result: TerminationResult,
    *,
    job_id: str | None,
    run_id: str | None,
    namespace: str | None,
) -> None:
    """Delete Kubernetes Jobs matching Goblin King labels when Kubernetes is configured."""
    try:
        from kubernetes import client, config

        try:
            config.load_incluster_config()
        except Exception:
            config.load_kube_config()
        batch = client.BatchV1Api()
        selected_namespace = namespace or _current_namespace()
        selector = ",".join(_label_filters(job_id, run_id))
        jobs = batch.list_namespaced_job(
            namespace=selected_namespace,
            label_selector=selector,
        )
        for job in jobs.items:
            name = job.metadata.name
            batch.delete_namespaced_job(
                name=name,
                namespace=selected_namespace,
                propagation_policy="Background",
            )
            result.killed.append(f"kubernetes:{selected_namespace}/{name}")
    except Exception as error:  # pragma: no cover - depends on optional cluster config
        result.errors.append(f"kubernetes termination unavailable: {error}")


def _current_namespace() -> str:
    """Return the in-cluster namespace when available, otherwise use default."""
    try:
        with open(
            "/var/run/secrets/kubernetes.io/serviceaccount/namespace",
            encoding="utf-8",
        ) as handle:
            return handle.read().strip() or "default"
    except OSError:
        return "default"
