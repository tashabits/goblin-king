"""Helpers for metadata persisted on jobs and runs."""

from __future__ import annotations

import json
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


def goblin_validation_input(
    definition: GoblinDefinition,
    runtime_input: dict[str, Any],
) -> dict[str, Any]:
    """Return an explicit contract-validation object without changing runtime input."""
    metadata = getattr(definition, "metadata", {}) or {}
    candidate = metadata.get("validation_input")
    if candidate is None:
        return runtime_input
    if not isinstance(candidate, dict):
        raise ValueError("goblin metadata validation_input must be a JSON object")
    try:
        decoded = json.loads(json.dumps(candidate))
    except (TypeError, ValueError) as error:
        raise ValueError("goblin metadata validation_input must be JSON-compatible") from error
    if not isinstance(decoded, dict):  # defensive against custom JSON encoders
        raise ValueError("goblin metadata validation_input must be a JSON object")
    return decoded
