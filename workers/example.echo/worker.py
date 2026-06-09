"""Self-contained Docker worker for the example.echo goblin."""

from __future__ import annotations

import json
import os
from pathlib import Path

from redis import Redis


def main() -> None:
    """Read the worker contract inputs, publish a result, and write fallback output."""
    input_payload = json.loads(Path(os.environ["GOBLIN_INPUT_PATH"]).read_text(encoding="utf-8"))
    context = json.loads(Path(os.environ["GOBLIN_CONTEXT_PATH"]).read_text(encoding="utf-8"))
    result_path = Path(os.environ["GOBLIN_RESULT_PATH"])
    redis_url = os.environ["GOBLIN_REDIS_URL"]
    run_id = os.environ["GOBLIN_RUN_ID"]

    message = input_payload.get("message", "")
    result = {
        "status": "success",
        "data": {
            "message": message,
            "echoed": input_payload,
            "run_id": context["run_id"],
        },
        "artifacts": [],
        "metrics": {"message_length": len(str(message))},
        "handoff": [],
        "error": None,
    }

    result_json = json.dumps(result)
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(result_json, encoding="utf-8")
    Redis.from_url(redis_url).set(f"goblin-king:results:{run_id}", result_json, ex=3600)


if __name__ == "__main__":
    main()
