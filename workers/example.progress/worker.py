"""Self-contained Docker worker for the example.progress goblin."""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path

from redis import Redis
from redis.exceptions import RedisError

KIND = "example.progress"


def main() -> None:
    """Publish progress heartbeats and a final progress result envelope."""
    input_payload = json.loads(Path(os.environ["GOBLIN_INPUT_PATH"]).read_text(encoding="utf-8"))
    context = json.loads(Path(os.environ["GOBLIN_CONTEXT_PATH"]).read_text(encoding="utf-8"))
    run_id = os.environ["GOBLIN_RUN_ID"]
    worker_id = os.environ["GOBLIN_WORKER_ID"]
    job_id = os.environ.get("GOBLIN_JOB_ID") or None
    steps = int(input_payload.get("steps") or 3)
    delay_seconds = min(max(float(input_payload.get("delay_seconds", 0.05)), 0.05), 5.0)

    timeline = []
    _run_event("stdout", {"text": f"starting {steps} progress steps\n"})
    time.sleep(delay_seconds)
    for index in range(1, max(1, steps) + 1):
        timeline.append({"step": index, "label": f"step-{index}", "status": "completed"})
        _heartbeat(f"step-{index}", worker_id, run_id, job_id)
        _run_event(
            "progress",
            {
                "percent": round(index / max(1, steps) * 100, 2),
                "message": f"completed step {index} of {max(1, steps)}",
            },
        )
        time.sleep(delay_seconds)
    _run_event("stdout", {"text": "progress run completed\n"})
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


def _run_event(event_type: str, payload: dict) -> None:
    """Best-effort append one bounded event using the optional additive contract."""
    required = {
        "GOBLIN_RUN_EVENT_STREAM",
        "GOBLIN_RUN_EVENT_SEQUENCE_KEY",
        "GOBLIN_RUN_EVENT_RATE_KEY",
    }
    if not required.issubset(os.environ):
        return
    try:
        payload_json = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        max_payload_bytes = int(os.environ["GOBLIN_RUN_EVENT_MAX_PAYLOAD_BYTES"])
        if len(payload_json.encode("utf-8")) > max_payload_bytes:
            return
        client = Redis.from_url(os.environ["GOBLIN_RUN_EVENT_REDIS_URL"])
        min_interval_ms = int(os.environ["GOBLIN_RUN_EVENT_MIN_INTERVAL_MS"])
        if not client.set(
            os.environ["GOBLIN_RUN_EVENT_RATE_KEY"],
            "1",
            nx=True,
            px=min_interval_ms,
        ):
            return
        sequence = int(client.incr(os.environ["GOBLIN_RUN_EVENT_SEQUENCE_KEY"]))
        event = json.dumps(
            {
                "sequence": sequence,
                "created_at": datetime.now(UTC).isoformat(),
                "event_type": event_type,
                "run_id": os.environ["GOBLIN_RUN_ID"],
                "payload": payload,
            },
            separators=(",", ":"),
        )
        ttl_seconds = int(os.environ["GOBLIN_RUN_EVENT_TTL_SECONDS"])
        pipe = client.pipeline(transaction=True)
        pipe.xadd(
            os.environ["GOBLIN_RUN_EVENT_STREAM"],
            {"event": event},
            maxlen=int(os.environ["GOBLIN_RUN_EVENT_MAX_EVENTS"]),
            approximate=False,
        )
        pipe.expire(os.environ["GOBLIN_RUN_EVENT_STREAM"], ttl_seconds)
        pipe.expire(os.environ["GOBLIN_RUN_EVENT_SEQUENCE_KEY"], ttl_seconds)
        pipe.execute()
    except (KeyError, RedisError, TypeError, ValueError):
        return


if __name__ == "__main__":
    main()
