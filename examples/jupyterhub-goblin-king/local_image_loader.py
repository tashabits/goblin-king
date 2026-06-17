"""Load locally built proof images into supported local Kubernetes clusters."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path


def load_images_for_current_context(images: list[str], kind_cluster: str) -> None:
    """Make local Docker images visible to the active Kubernetes context."""
    if _load_kind_images(images, kind_cluster):
        return
    if _current_context() == "docker-desktop":
        _load_docker_desktop_images(images)


def _load_kind_images(images: list[str], kind_cluster: str) -> bool:
    try:
        completed = subprocess.run(
            ["kind", "get", "clusters"],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return False
    clusters = {line.strip() for line in completed.stdout.splitlines()}
    if completed.returncode != 0 or kind_cluster not in clusters:
        return False
    for image in images:
        subprocess.run(
            ["kind", "load", "docker-image", image, "--name", kind_cluster],
            check=True,
        )
    return True


def _current_context() -> str:
    completed = subprocess.run(
        ["kubectl", "config", "current-context"],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _load_docker_desktop_images(images: list[str]) -> None:
    nodes = _kubectl_stdout(["get", "nodes", "-o", "jsonpath={.items[*].metadata.name}"]).split()
    for node in nodes:
        pod = _start_node_debug_pod(node)
        try:
            subprocess.run(
                ["kubectl", "wait", f"pod/{pod}", "--for=condition=Ready", "--timeout=90s"],
                check=True,
            )
            for image in images:
                _import_image_with_node_debug_pod(image, pod)
        finally:
            subprocess.run(
                ["kubectl", "delete", "pod", pod, "--ignore-not-found"],
                check=False,
            )


def _start_node_debug_pod(node: str) -> str:
    completed = subprocess.run(
        ["kubectl", "debug", f"node/{node}", "--image=busybox", "--", "sleep", "3600"],
        check=True,
        capture_output=True,
        text=True,
    )
    match = re.search(r"Creating debugging pod (\S+)", completed.stdout + completed.stderr)
    if match is None:
        raise RuntimeError(f"Could not determine debug pod name for node {node}")
    return match.group(1)


def _import_image_with_node_debug_pod(image: str, pod: str) -> None:
    archive = _save_image(image)
    host_name = _safe_image_archive_name(image)
    try:
        subprocess.run(
            ["kubectl", "cp", str(archive), f"{pod}:/host/tmp/{host_name}", "-c", "debugger"],
            check=True,
        )
        subprocess.run(
            [
                "kubectl",
                "exec",
                pod,
                "-c",
                "debugger",
                "--",
                "chroot",
                "/host",
                "/usr/local/bin/ctr",
                "-n",
                "k8s.io",
                "images",
                "import",
                f"/tmp/{host_name}",
            ],
            check=True,
        )
    finally:
        subprocess.run(
            [
                "kubectl",
                "exec",
                pod,
                "-c",
                "debugger",
                "--",
                "chroot",
                "/host",
                "rm",
                "-f",
                f"/tmp/{host_name}",
            ],
            check=False,
        )
        archive.unlink(missing_ok=True)


def _save_image(image: str) -> Path:
    archive_dir = Path(".runlogs") / "image-archives"
    archive_dir.mkdir(parents=True, exist_ok=True)
    archive = archive_dir / f"{os.getpid()}-{_safe_image_archive_name(image)}"
    archive.unlink(missing_ok=True)
    try:
        subprocess.run(["docker", "save", "-o", str(archive), image], check=True)
    except Exception:
        archive.unlink(missing_ok=True)
        raise
    return archive


def _safe_image_archive_name(image: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", image).strip("-") + ".tar"


def _kubectl_stdout(args: list[str]) -> str:
    completed = subprocess.run(
        ["kubectl", *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()
