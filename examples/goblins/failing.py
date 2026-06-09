"""Example failing goblin used by local runtime and CLI tests.

GOBLIN_KIND: example.fail
Expected input: any JSON object.
Result shape: none; this goblin raises intentionally.
Side effects: none.
Artifacts: none.
Failure modes: always raises `RuntimeError`.
"""

from __future__ import annotations

from typing import Any

from goblin_king.contracts import GoblinContext

GOBLIN_KIND = "example.fail"


def run(input: dict[str, Any], ctx: GoblinContext) -> dict[str, Any]:
    """Raise a deterministic error so failure persistence can be tested."""
    raise RuntimeError("intentional failure")
