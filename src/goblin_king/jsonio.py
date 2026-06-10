"""Small JSON file helpers used by Goblin King internals."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def read_json_file(path: Path) -> Any:
    """Read and decode UTF-8 JSON from a local path."""
    return json.loads(path.read_text(encoding="utf-8"))


def read_json_object(path: Path) -> dict[str, Any]:
    """Read a UTF-8 JSON file and require the top-level value to be an object."""
    payload = read_json_file(path)
    if not isinstance(payload, dict):
        raise TypeError("JSON document must be an object")
    return payload


def pretty_json(value: Any) -> str:
    """Serialize JSON with Goblin King's stable human-readable formatting."""
    return json.dumps(value, indent=2)


def pretty_json_line(value: Any) -> str:
    """Serialize pretty JSON with a trailing newline for generated files."""
    return pretty_json(value) + "\n"
