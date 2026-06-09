"""Progress sample goblin that reports deterministic step metrics."""

from __future__ import annotations

from typing import Any

from goblin_king.contracts import GoblinContext, GoblinResult

GOBLIN_KIND = "example.progress"


def run(input_payload: dict[str, Any], ctx: GoblinContext) -> GoblinResult:
    """Return a compact progress timeline for event and admin demos."""
    steps = int(input_payload.get("steps") or 3)
    timeline = [
        {"step": index, "label": f"step-{index}", "status": "completed"}
        for index in range(1, max(1, steps) + 1)
    ]
    return GoblinResult.ok(
        data={"message": "progress completed", "timeline": timeline, "run_id": ctx.run_id},
        metrics={"steps": len(timeline), "percent_complete": 100},
        handoff=[{"kind": "progress.summary", "payload": {"steps": len(timeline)}}],
    )
