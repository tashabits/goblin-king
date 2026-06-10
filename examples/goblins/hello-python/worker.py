"""Minimal Python worker that implements the Goblin container contract."""

from __future__ import annotations

import json
import os
from pathlib import Path


def read_json_env(name: str) -> dict:
    path = Path(os.environ[name])
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"{name} must point to a JSON object")
    return value


def main() -> None:
    input_data = read_json_env("GOBLIN_INPUT_PATH")
    read_json_env("GOBLIN_CONTEXT_PATH")
    result_path = Path(os.environ["GOBLIN_RESULT_PATH"])
    run_id = os.environ.get("GOBLIN_RUN_ID", "unknown-run")
    kind = os.environ.get("GOBLIN_KIND", "example.hello-python")
    target = input_data.get("target", "World")

    print(f"Python goblin says hello to {target}. The crown enjoys stdlib.")
    result = {
        "status": "success",
        "data": {
            "message": "Hello World",
            "language": "python",
            "runtime": "Python 3.12 standard library",
            "kind": kind,
            "run_id": run_id,
            "target": target,
            "input": input_data,
            "quote": "A goblin with no dependencies travels lightly.",
        },
        "artifacts": [],
        "metrics": {},
        "handoff": [],
        "error": None,
    }
    result_path.write_text(json.dumps(result, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
