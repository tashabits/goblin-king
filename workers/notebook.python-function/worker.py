"""Run a notebook-defined Python function through the container worker contract."""

from __future__ import annotations

import inspect
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from redis import Redis


def main() -> None:
    """Load the declared function bundle, call it, and publish a result envelope."""
    input_payload = json.loads(Path(os.environ["GOBLIN_INPUT_PATH"]).read_text(encoding="utf-8"))
    context = json.loads(Path(os.environ["GOBLIN_CONTEXT_PATH"]).read_text(encoding="utf-8"))
    run_id = os.environ["GOBLIN_RUN_ID"]
    worker_id = os.environ["GOBLIN_WORKER_ID"]
    job_id = os.environ.get("GOBLIN_JOB_ID") or None
    kind = str(input_payload.get("kind") or "notebook.python-function")

    _heartbeat("running", worker_id, run_id, job_id, kind)
    try:
        result = _call_declared_function(input_payload, context)
    except Exception as error:
        result = {
            "status": "failed",
            "data": {},
            "artifacts": [],
            "metrics": {},
            "handoff": [],
            "error": f"{kind} failed: {error}",
        }
    _publish_result(run_id, _normalize_result(result))
    _heartbeat("completed", worker_id, run_id, job_id, kind)


def _call_declared_function(input_payload: dict[str, Any], context: dict[str, Any]) -> Any:
    source = str(input_payload["source"])
    function_name = str(input_payload.get("function") or "run")
    payload = input_payload.get("payload")
    if not isinstance(payload, dict):
        raise ValueError("notebook function payload must be a JSON object")

    namespace: dict[str, Any] = {"__name__": "__notebook_goblin__"}
    exec(compile(source, "<notebook-goblin>", "exec"), namespace)
    function = namespace.get(function_name)
    if not callable(function):
        raise ValueError(f"function not found or not callable: {function_name}")

    parameters = inspect.signature(function).parameters
    if len(parameters) >= 2:
        return function(payload, context)
    return function(payload)


def _normalize_result(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict) and raw.get("status") in {"success", "failed"}:
        return {
            "status": raw["status"],
            "data": raw.get("data") or {},
            "artifacts": raw.get("artifacts") or [],
            "metrics": raw.get("metrics") or {},
            "handoff": raw.get("handoff") or [],
            "error": raw.get("error"),
        }
    if isinstance(raw, dict):
        data = raw
    elif raw is None:
        data = {}
    else:
        data = {"result": raw}
    json.dumps(data)
    return {
        "status": "success",
        "data": data,
        "artifacts": [],
        "metrics": {},
        "handoff": [],
        "error": None,
    }


def _publish_result(run_id: str, result: dict[str, Any]) -> None:
    result_json = json.dumps(result)
    result_path = Path(os.environ["GOBLIN_RESULT_PATH"])
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(result_json, encoding="utf-8")
    Redis.from_url(os.environ["GOBLIN_REDIS_URL"]).set(
        f"goblin-king:results:{run_id}",
        result_json,
        ex=3600,
    )


def _heartbeat(status: str, worker_id: str, run_id: str, job_id: str | None, kind: str) -> None:
    payload = {
        "owner_id": worker_id,
        "owner_type": "worker",
        "status": status,
        "last_seen_at": datetime.now(UTC).isoformat(),
        "job_id": job_id,
        "run_id": run_id,
        "payload": {"kind": kind},
    }
    encoded = json.dumps(payload)
    client = Redis.from_url(os.environ["GOBLIN_HEARTBEAT_REDIS_URL"])
    client.rpush(os.environ["GOBLIN_HEARTBEAT_KEY"], encoded)
    client.publish(os.environ["GOBLIN_HEARTBEAT_CHANNEL"], encoded)


if __name__ == "__main__":
    main()
