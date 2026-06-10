"""Timeout-ish and cancellation-friendly behavior sample."""

import json
import os
import signal
import sys
import time
from pathlib import Path

cancelled = False


def _handle_term(_signum: int, _frame: object) -> None:
    global cancelled
    cancelled = True


def _write_result(status: str, message: str, error: str | None = None) -> None:
    result = {
        "status": status,
        "data": {
            "message": message,
            "language": "python",
            "run_id": os.environ.get("GOBLIN_RUN_ID", "unknown-run"),
        },
        "artifacts": [],
        "metrics": {"cancelled": cancelled},
        "handoff": [],
        "error": error,
    }
    Path(os.environ["GOBLIN_RESULT_PATH"]).write_text(
        json.dumps(result, indent=2),
        encoding="utf-8",
    )


def main() -> None:
    signal.signal(signal.SIGTERM, _handle_term)
    input_data = json.loads(Path(os.environ["GOBLIN_INPUT_PATH"]).read_text(encoding="utf-8"))
    json.loads(Path(os.environ["GOBLIN_CONTEXT_PATH"]).read_text(encoding="utf-8"))
    ticks = int(input_data.get("ticks", 3))
    for tick in range(1, ticks + 1):
        if cancelled:
            print("Python slow goblin received cancellation. The crown lowers its voice.")
            _write_result("failed", "Cancelled before completion", "cancelled by signal")
            sys.exit(2)
        print(json.dumps({"event": "slow_tick", "tick": tick, "total": ticks}), flush=True)
        time.sleep(0.1)
    _write_result("success", "Slow cancellable work complete")


if __name__ == "__main__":
    main()
