"""Registry definition target for the long-running Hello World service goblin."""

from __future__ import annotations

from typing import Any

from goblin_king.contracts import GoblinContext, GoblinResult

GOBLIN_KIND = "example.long-hello"


def run(_input_payload: dict[str, Any], _ctx: GoblinContext) -> GoblinResult:
    """Explain that this goblin is intended to run as a registered service."""
    return GoblinResult.ok(
        data={
            "message": "example.long-hello is a long-running service worker",
            "probe_path": "/hello",
        }
    )
