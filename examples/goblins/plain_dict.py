"""Example plain-dict goblin used to prove result coercion.

GOBLIN_KIND: example.plain
Expected input: any JSON object.
Result shape: plain dictionary.
Side effects: none.
Artifacts: none.
Failure modes: none expected for valid JSON object input.
"""

from __future__ import annotations

from typing import Any

from goblin_king.contracts import GoblinContext

GOBLIN_KIND = "example.plain"


def run(input: dict[str, Any], ctx: GoblinContext) -> dict[str, Any]:
    """Return a plain dictionary that the runtime wraps in `GoblinResult.ok`."""
    return {"plain": input, "run_id": ctx.run_id}
