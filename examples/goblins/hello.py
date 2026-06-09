"""Short-running Hello World goblin used by admin and deployment smoke tests."""

from __future__ import annotations

from typing import Any

from goblin_king.contracts import GoblinContext, GoblinResult

GOBLIN_KIND = "example.hello"


def run(input_payload: dict[str, Any], ctx: GoblinContext) -> GoblinResult:
    """Return a tiny successful result that proves one-shot execution works."""
    name = str(input_payload.get("name") or "World")
    return GoblinResult.ok(
        data={
            "message": f"Hello {name}",
            "canonical_message": "Hello World",
            "run_id": ctx.run_id,
        },
        metrics={"input_keys": len(input_payload)},
    )
