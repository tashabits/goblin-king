"""Deterministic text-normalization worker for the portable backbone example."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

KIND = "example.worker-backbone.normalize-note"


def normalize_note(payload: dict[str, Any]) -> dict[str, Any]:
    """Return normalized text and simple metrics from the input payload."""
    original = str(payload.get("text", ""))
    normalized = original.strip()
    if payload.get("collapse_whitespace", True):
        normalized = re.sub(r"\s+", " ", normalized)

    case = payload.get("case", "preserve")
    if case == "lower":
        normalized = normalized.lower()
    elif case == "upper":
        normalized = normalized.upper()

    words = [word for word in normalized.split(" ") if word]
    return {
        "normalized": normalized,
        "original_length": len(original),
        "normalized_length": len(normalized),
        "word_count": len(words),
    }


def build_result(payload: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    """Build the portable worker result envelope."""
    data = normalize_note(payload)
    return {
        "status": "success",
        "data": {
            "kind": KIND,
            "run_id": context.get("run_id"),
            **data,
        },
        "artifacts": [],
        "metrics": {
            "original_length": data["original_length"],
            "normalized_length": data["normalized_length"],
            "word_count": data["word_count"],
        },
        "handoff": [],
        "error": None,
    }


def main() -> None:
    """Read contract files and write the result envelope."""
    payload = _read_json_env("GOBLIN_INPUT_PATH")
    context = _read_json_env("GOBLIN_CONTEXT_PATH")
    result = build_result(payload, context)
    _write_json(Path(os.environ["GOBLIN_RESULT_PATH"]), result)


def _read_json_env(name: str) -> dict[str, Any]:
    path = Path(os.environ[name])
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


if __name__ == "__main__":
    main()

