"""Controlled failure sample goblin for admin proof and retry demonstrations."""

from __future__ import annotations

from typing import Any

from goblin_king.contracts import GoblinContext, GoblinResult

GOBLIN_KIND = "example.controlled-failure"


def run(input_payload: dict[str, Any], _ctx: GoblinContext) -> GoblinResult:
    """Return a predictable failed result without crashing the host runtime."""
    reason = str(input_payload.get("reason") or "controlled failure requested")
    return GoblinResult.failed(
        error=reason,
        data={"expected": True, "kind": GOBLIN_KIND},
        metrics={"failed_on_purpose": True},
    )
