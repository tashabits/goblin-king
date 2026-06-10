"""Helpers for metadata persisted on jobs and runs."""

from __future__ import annotations

from typing import Any

from goblin_king.contracts import GoblinDefinition


def goblin_job_metadata(
    definition: GoblinDefinition,
    policy: Any | None = None,
) -> dict[str, Any]:
    """Return source and effective-policy metadata for a queued goblin job."""
    metadata = getattr(definition, "metadata", {}) or {}
    payload: dict[str, Any] = {
        "goblin_source": metadata.get("source", "registry"),
        "goblin_definition": definition.model_dump(mode="json"),
    }
    if policy is not None:
        payload["resource_policy"] = policy.compact()
    return payload
