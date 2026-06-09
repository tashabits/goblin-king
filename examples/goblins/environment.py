"""Environment and context introspection sample goblin."""

from __future__ import annotations

import os
import platform
from typing import Any

from goblin_king.contracts import GoblinContext, GoblinResult

GOBLIN_KIND = "example.environment"


def run(input_payload: dict[str, Any], ctx: GoblinContext) -> GoblinResult:
    """Return safe context and runtime information for deployment diagnostics."""
    selected_env = {
        name: os.environ[name]
        for name in sorted(os.environ)
        if name.startswith(("GOBLIN_", "PYTHON"))
    }
    return GoblinResult.ok(
        data={
            "platform": platform.platform(),
            "python": platform.python_version(),
            "input": input_payload,
            "context": ctx.model_dump(mode="json"),
            "environment": selected_env,
        },
        metrics={"environment_keys": len(selected_env)},
    )
