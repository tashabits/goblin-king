"""Runtime adapters execute goblin definitions and normalize their result envelopes."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from goblin_king.contracts import GoblinContext, GoblinDefinition, GoblinResult


class InProcessRuntime:
    """Execute trusted goblin code directly in the current Python process for Phase 1."""

    def run(
        self,
        definition: GoblinDefinition,
        entrypoint: Callable[[dict[str, Any], GoblinContext], Any],
        input_payload: dict[str, Any],
        context: GoblinContext,
    ) -> GoblinResult:
        """Call a goblin entrypoint and convert supported return values into GoblinResult."""
        try:
            raw_result = entrypoint(input_payload, context)
        except Exception as error:  # pragma: no cover - exact exception type is goblin-owned
            return GoblinResult.failed(error=f"{definition.kind} failed: {error}")

        if isinstance(raw_result, GoblinResult):
            return raw_result
        if isinstance(raw_result, dict):
            return GoblinResult.ok(data=raw_result)
        return GoblinResult.failed(
            error=f"{definition.kind} returned unsupported result type {type(raw_result).__name__}"
        )
