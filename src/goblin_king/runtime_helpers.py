"""Internal helpers shared by Docker and Kubernetes runtime adapters."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

from goblin_king.contracts import GoblinResult
from goblin_king.resource_policies import ResourcePolicy


def container_redis_url(redis_url: str) -> str:
    """Translate host-local Redis URLs into a Docker-container-reachable URL."""
    parsed = urlparse(redis_url)
    if parsed.hostname not in {"localhost", "127.0.0.1"}:
        return redis_url
    netloc = parsed.netloc.replace(parsed.hostname, "host.docker.internal", 1)
    return urlunparse(parsed._replace(netloc=netloc))


def docker_policy_args(policy: ResourcePolicy) -> list[str]:
    """Translate supported policy fields into docker run arguments."""
    args: list[str] = []
    if policy.cpu.limit:
        cpu_limit = (
            str(float(policy.cpu.limit[:-1]) / 1000)
            if policy.cpu.limit.endswith("m")
            else policy.cpu.limit
        )
        args.extend(["--cpus", cpu_limit])
    if policy.memory.limit:
        args.extend(["--memory", docker_memory_quantity(policy.memory.limit)])
    if policy.process.pids_limit is not None:
        args.extend(["--pids-limit", str(policy.process.pids_limit)])
    if policy.network.mode:
        args.extend(["--network", policy.network.mode])
    if policy.filesystem.read_only_root:
        args.append("--read-only")
    for tmpfs_path in policy.filesystem.tmpfs:
        args.extend(["--tmpfs", tmpfs_path])
    if policy.logs.max_bytes is not None:
        args.extend(["--log-opt", f"max-size={policy.logs.max_bytes}"])
    return args


def docker_memory_quantity(value: str) -> str:
    """Normalize Kubernetes-style memory units into Docker CLI-friendly units."""
    replacements = {
        "Ki": "k",
        "Mi": "m",
        "Gi": "g",
        "Ti": "t",
    }
    for suffix, docker_suffix in replacements.items():
        if value.endswith(suffix):
            return f"{value.removesuffix(suffix)}{docker_suffix}"
    return value


def kubernetes_policy_fields(policy: ResourcePolicy) -> dict[str, Any]:
    """Translate supported policy fields into Kubernetes container fields."""
    fields: dict[str, Any] = {}
    requests: dict[str, str] = {}
    limits: dict[str, str] = {}
    if policy.cpu.request:
        requests["cpu"] = policy.cpu.request
    if policy.memory.request:
        requests["memory"] = policy.memory.request
    if policy.cpu.limit:
        limits["cpu"] = policy.cpu.limit
    if policy.memory.limit:
        limits["memory"] = policy.memory.limit
    if requests or limits:
        fields["resources"] = {}
        if requests:
            fields["resources"]["requests"] = requests
        if limits:
            fields["resources"]["limits"] = limits
    if policy.filesystem.read_only_root:
        fields["securityContext"] = {"readOnlyRootFilesystem": True}
    return fields


def artifact_policy_error(
    result: GoblinResult,
    policy: ResourcePolicy | None,
    artifact_root: Path,
) -> str | None:
    """Return an artifact policy violation message for result metadata, if any."""
    if policy is None:
        return None
    max_files = policy.filesystem.artifact_max_files
    if max_files is not None and len(result.artifacts) > max_files:
        return f"artifact file count exceeds policy: {len(result.artifacts)} > {max_files}"
    max_bytes = policy.filesystem.artifact_max_bytes
    if max_bytes is None:
        return None
    total = 0
    for artifact in result.artifacts:
        size = result.metrics.get(f"artifact.{artifact.name}.bytes")
        if isinstance(size, int | float):
            total += int(size)
            continue
        if artifact.uri.startswith("file://"):
            path = Path(artifact.uri.removeprefix("file://"))
        else:
            path = Path(artifact.uri)
        if not path.is_absolute():
            path = artifact_root / path
        try:
            resolved = path.resolve()
            resolved.relative_to(artifact_root.resolve())
        except ValueError:
            continue
        if resolved.is_file():
            total += resolved.stat().st_size
    if total > max_bytes:
        return f"artifact bytes exceed policy: {total} > {max_bytes}"
    return None


def current_kubernetes_namespace() -> str:
    """Return the in-cluster namespace, falling back to default for local tests."""
    namespace_path = Path("/var/run/secrets/kubernetes.io/serviceaccount/namespace")
    if namespace_path.exists():
        return namespace_path.read_text(encoding="utf-8").strip()
    return os.environ.get("GOBLIN_KING_K8S_NAMESPACE", "default")


def kubernetes_clients() -> tuple[Any, Any]:
    """Create Kubernetes API clients using in-cluster config or local kubeconfig."""
    from kubernetes import client, config

    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()
    return client.BatchV1Api(), client.CoreV1Api()


def kubernetes_name(value: str) -> str:
    """Normalize a run-specific value into a valid Kubernetes object name."""
    lowered = "".join(character if character.isalnum() else "-" for character in value.lower())
    normalized = "-".join(part for part in lowered.split("-") if part)
    return normalized[:63].strip("-") or "goblin-worker"
