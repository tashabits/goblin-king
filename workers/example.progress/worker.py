"""Self-contained Docker worker for the example.progress goblin."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

from redis import Redis

KIND = "example.progress"


def main() -> None:
    """Publish progress heartbeats and a final progress result envelope."""
    input_payload = json.loads(Path(os.environ["GOBLIN_INPUT_PATH"]).read_text(encoding="utf-8"))
    context = json.loads(Path(os.environ["GOBLIN_CONTEXT_PATH"]).read_text(encoding="utf-8"))
    run_id = os.environ["GOBLIN_RUN_ID"]
    worker_id = os.environ["GOBLIN_WORKER_ID"]
    job_id = os.environ.get("GOBLIN_JOB_ID") or None
    steps = int(input_payload.get("steps") or 3)

    timeline = []
    for index in range(1, max(1, steps) + 1):
        timeline.append({"step": index, "label": f"step-{index}", "status": "completed"})
        _heartbeat(f"step-{index}", worker_id, run_id, job_id)
    _publish_result(
        run_id,
        {
            "status": "success",
            "data": {
                "message": "progress completed",
                "timeline": timeline,
                "run_id": context["run_id"],
            },
            "artifacts": [],
            "metrics": {"steps": len(timeline), "percent_complete": 100},
            "handoff": [{"kind": "progress.summary", "payload": {"steps": len(timeline)}}],
            "error": None,
        },
    )
    _heartbeat("completed", worker_id, run_id, job_id)


def _publish_result(run_id: str, result: dict) -> None:
    result_json = json.dumps(result)
    Path(os.environ["GOBLIN_RESULT_PATH"]).write_text(result_json, encoding="utf-8")
    Redis.from_url(os.environ["GOBLIN_REDIS_URL"]).set(
        f"goblin-king:results:{run_id}",
        result_json,
        ex=3600,
    )


def _heartbeat(status: str, worker_id: str, run_id: str, job_id: str | None) -> None:
    payload = {
        "owner_id": worker_id,
        "owner_type": "worker",
        "status": status,
        "last_seen_at": datetime.now(UTC).isoformat(),
        "job_id": job_id,
        "run_id": run_id,
        "payload": {"kind": KIND},
    }
    encoded = json.dumps(payload)
    client = Redis.from_url(os.environ["GOBLIN_HEARTBEAT_REDIS_URL"])
    client.rpush(os.environ["GOBLIN_HEARTBEAT_KEY"], encoded)
    client.publish(os.environ["GOBLIN_HEARTBEAT_CHANNEL"], encoded)


if __name__ == "__main__":
    main()
