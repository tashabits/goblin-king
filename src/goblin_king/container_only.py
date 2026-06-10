"""Placeholder entrypoint for goblins that are defined only as containers."""

from __future__ import annotations

from typing import Any


def run(_input_payload: dict[str, Any], _context: Any) -> None:
    """Fail clearly when a container-only goblin is run in-process."""
    raise RuntimeError("container-only goblins must run with the docker or kubernetes runtime")
