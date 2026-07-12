"""Inline program used by the Kubernetes result-forwarder sidecar."""

RESULT_FORWARDER_SCRIPT = r"""
import os
import time
from pathlib import Path

from redis import Redis

run_id = os.environ["GOBLIN_RUN_ID"]
redis_url = os.environ["GOBLIN_REDIS_URL"]
result_path = Path(os.environ["GOBLIN_RESULT_PATH"])
wait_seconds = int(os.environ.get("GOBLIN_RESULT_WAIT_SECONDS", "300"))
deadline = time.monotonic() + wait_seconds

while time.monotonic() < deadline:
    if result_path.is_file():
        Redis.from_url(redis_url).set(
            f"goblin-king:results:{run_id}",
            result_path.read_text(encoding="utf-8"),
            ex=3600,
        )
        raise SystemExit(0)
    time.sleep(0.25)

raise SystemExit(f"result file not found before timeout: {result_path}")
"""
