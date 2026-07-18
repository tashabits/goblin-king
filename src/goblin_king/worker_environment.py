"""Safe project-owned literal environment shared by container runtimes."""

from __future__ import annotations

from goblin_king.contracts import GoblinDefinition


def literal_worker_environment(definition: GoblinDefinition) -> dict[str, str]:
    """Normalize safe literal environment values from project goblin metadata."""
    metadata_env = definition.metadata.get("env", {})
    if not isinstance(metadata_env, dict):
        return {}
    return {
        str(key): str(value)
        for key, value in metadata_env.items()
        if str(key) and value is not None
    }
