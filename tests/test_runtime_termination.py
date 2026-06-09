"""Tests for scoped runtime termination helpers."""

from __future__ import annotations

import subprocess

from goblin_king.termination import terminate_runtime


def test_docker_termination_uses_goblin_labels(monkeypatch) -> None:
    """Verify Docker hard-kill only targets containers with Goblin King labels."""
    calls: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs) -> subprocess.CompletedProcess:
        calls.append(command)
        if command[:3] == ["docker", "ps", "-q"]:
            return subprocess.CompletedProcess(command, 0, stdout="container-1\n", stderr="")
        return subprocess.CompletedProcess(command, 0, stdout="container-1\n", stderr="")

    monkeypatch.setattr("goblin_king.termination.subprocess.run", fake_run)

    result = terminate_runtime(job_id="job-1", run_id="run-1", runtime="docker")

    assert result.killed == ["docker:container-1"]
    assert "--filter" in calls[0]
    assert "label=goblin-king.worker=true" in calls[0]
    assert "label=goblin-king.job-id=job-1" in calls[0]
    assert "label=goblin-king.run-id=run-1" in calls[0]
    assert calls[1] == ["docker", "kill", "container-1"]
