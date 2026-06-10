"""Progress/logging behavior sample for the Goblin container contract."""

import json
import os
import time
from pathlib import Path


def main() -> None:
    input_data = json.loads(Path(os.environ["GOBLIN_INPUT_PATH"]).read_text(encoding="utf-8"))
    context = json.loads(Path(os.environ["GOBLIN_CONTEXT_PATH"]).read_text(encoding="utf-8"))
    steps = int(input_data.get("steps", 3))
    for step in range(1, steps + 1):
        print(json.dumps({"event": "progress", "step": step, "total": steps}), flush=True)
        time.sleep(0.05)

    result = {
        "status": "success",
        "data": {
            "message": "Progress complete",
            "language": "python",
            "run_id": context["run_id"],
            "steps": steps,
        },
        "artifacts": [],
        "metrics": {"steps": steps},
        "handoff": [],
        "error": None,
    }
    Path(os.environ["GOBLIN_RESULT_PATH"]).write_text(
        json.dumps(result, indent=2),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
