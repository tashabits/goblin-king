"""Example echo goblin documenting the minimum goblin authoring contract.

GOBLIN_KIND: example.echo
Expected input: any JSON object.
Result shape: returns the input under `echo` plus the current run ID.
Side effects: none.
Artifacts: none.
Failure modes: none expected for valid JSON object input.
"""

from __future__ import annotations

from typing import Any

from goblin_king.contracts import GoblinContext, GoblinResult

GOBLIN_KIND = "example.echo"


def run(input: dict[str, Any], ctx: GoblinContext) -> GoblinResult:
    """Return the submitted input and run context so the vertical slice is inspectable."""
    return GoblinResult.ok(data={"echo": input, "run_id": ctx.run_id})
