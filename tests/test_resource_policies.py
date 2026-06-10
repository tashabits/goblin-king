"""Tests for runtime resource policy loading and effective policy enforcement."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from goblin_king.resource_policies import ResourcePolicyError, ResourcePolicySet


def write_policy(path: Path, payload: dict[str, object]) -> Path:
    """Write a compact policy fixture and return its path."""
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_resource_policy_defaults_and_overrides_merge(tmp_path: Path) -> None:
    """Verify per-goblin overrides merge with defaults and remain JSON-ready."""
    policy_path = write_policy(
        tmp_path / "policies.json",
        {
            "version": 1,
            "defaults": {
                "timeout_seconds": 30,
                "max_retries": 1,
                "cpu": {"limit": "500m"},
                "memory": {"limit": "256Mi"},
            },
            "goblins": {
                "example.echo": {
                    "timeout_seconds": 90,
                    "filesystem": {"artifact_max_files": 2},
                }
            },
            "ceilings": {
                "timeout_seconds": 120,
                "max_retries": 3,
                "cpu": {"limit": "1"},
                "memory": {"limit": "1Gi"},
                "filesystem": {"artifact_max_files": 10},
            },
        },
    )

    policies = ResourcePolicySet.from_path(policy_path)
    policy = policies.effective_for("example.echo")

    assert policy.timeout_seconds == 90
    assert policy.max_retries == 1
    assert policy.cpu.limit == "500m"
    assert policy.memory.limit == "256Mi"
    assert policy.filesystem.artifact_max_files == 2
    assert policy.compact()["filesystem"]["artifact_max_files"] == 2


def test_resource_policy_rejects_unknown_fields(tmp_path: Path) -> None:
    """Verify unknown policy fields fail clearly, including nested sections."""
    cases = [
        ({"defaultz": {}}, "defaultz"),
        ({"defaults": {"cpu": {"shares": 2}}}, "shares"),
        ({"goblins": {"example.echo": {"filesystem": {"scratch": "/tmp"}}}}, "scratch"),
    ]
    for index, (payload, expected_field) in enumerate(cases):
        policy_path = write_policy(
            tmp_path / f"unknown-{index}.json",
            {"version": 1, **payload},
        )

        with pytest.raises(ResourcePolicyError, match=expected_field):
            ResourcePolicySet.from_path(policy_path)


def test_resource_policy_rejects_above_ceiling(tmp_path: Path) -> None:
    """Verify effective policy values above configured ceilings fail before launch."""
    cases = [
        (
            {"timeout_seconds": 120},
            {"timeout_seconds": 60},
            "timeout_seconds",
        ),
        (
            {"max_retries": 4},
            {"max_retries": 2},
            "max_retries",
        ),
        (
            {"cpu": {"limit": "2"}},
            {"cpu": {"limit": "1"}},
            "cpu.limit",
        ),
        (
            {"memory": {"limit": "4Gi"}},
            {"memory": {"limit": "1Gi"}},
            "memory.limit",
        ),
        (
            {"process": {"pids_limit": 128}},
            {"process": {"pids_limit": 64}},
            "process.pids_limit",
        ),
        (
            {"filesystem": {"artifact_max_bytes": 2000}},
            {"filesystem": {"artifact_max_bytes": 1000}},
            "filesystem.artifact_max_bytes",
        ),
        (
            {"filesystem": {"artifact_max_files": 20}},
            {"filesystem": {"artifact_max_files": 10}},
            "filesystem.artifact_max_files",
        ),
        (
            {"logs": {"max_bytes": 2000}},
            {"logs": {"max_bytes": 1000}},
            "logs.max_bytes",
        ),
        (
            {"concurrency": {"max_running": 4}},
            {"concurrency": {"max_running": 2}},
            "concurrency.max_running",
        ),
        (
            {"concurrency": {"max_project_running": 4}},
            {"concurrency": {"max_project_running": 2}},
            "concurrency.max_project_running",
        ),
    ]
    for index, (defaults, ceilings, expected_field) in enumerate(cases):
        policy_path = write_policy(
            tmp_path / f"policies-{index}.json",
            {
                "version": 1,
                "defaults": defaults,
                "ceilings": ceilings,
            },
        )
        policies = ResourcePolicySet.from_path(policy_path)

        with pytest.raises(ResourcePolicyError, match=expected_field):
            policies.effective_for("example.echo")


def test_resource_policy_rejects_unsupported_version(tmp_path: Path) -> None:
    """Verify versioned policy files fail clearly when unsupported."""
    policy_path = write_policy(tmp_path / "policies.json", {"version": 99})

    with pytest.raises(ResourcePolicyError, match="unsupported resource policy version"):
        ResourcePolicySet.from_path(policy_path)
