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


def test_resource_policy_rejects_above_ceiling(tmp_path: Path) -> None:
    """Verify effective policy values above configured ceilings fail before launch."""
    policy_path = write_policy(
        tmp_path / "policies.json",
        {
            "version": 1,
            "defaults": {"memory": {"limit": "4Gi"}},
            "ceilings": {"memory": {"limit": "1Gi"}},
        },
    )

    policies = ResourcePolicySet.from_path(policy_path)

    with pytest.raises(ResourcePolicyError, match="memory.limit"):
        policies.effective_for("example.echo")


def test_resource_policy_rejects_unsupported_version(tmp_path: Path) -> None:
    """Verify versioned policy files fail clearly when unsupported."""
    policy_path = write_policy(tmp_path / "policies.json", {"version": 99})

    with pytest.raises(ResourcePolicyError, match="unsupported resource policy version"):
        ResourcePolicySet.from_path(policy_path)
