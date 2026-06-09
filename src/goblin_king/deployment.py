"""Small deployment orchestration helpers for local proof and admin records."""

from __future__ import annotations

import subprocess
from pathlib import Path


def helm_template_command(
    *,
    chart: str | Path,
    release: str,
    namespace: str | None = None,
    values: str | Path | None = None,
) -> list[str]:
    """Build a deterministic Helm template command for proof records."""
    command = ["helm", "template", release, str(chart)]
    if namespace:
        command.extend(["--namespace", namespace])
    if values:
        command.extend(["-f", str(values)])
    return command


def run_command(command: list[str], *, cwd: str | Path | None = None) -> tuple[int, str]:
    """Run one local orchestration command and return combined proof output."""
    completed = subprocess.run(
        command,
        cwd=str(cwd) if cwd else None,
        check=False,
        capture_output=True,
        text=True,
    )
    output = "\n".join(part for part in [completed.stdout, completed.stderr] if part)
    return completed.returncode, output.strip()


def image_push_command(image: str) -> list[str]:
    """Return the generic Docker push command for a promoted worker image."""
    return ["docker", "push", image]


def image_inspect_command(image: str) -> list[str]:
    """Return the generic Docker inspect command used to look up image digests."""
    return ["docker", "image", "inspect", image, "--format={{json .RepoDigests}}"]
