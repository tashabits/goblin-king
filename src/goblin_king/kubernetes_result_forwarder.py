"""Publish Kubernetes results only after declared artifact bytes are retained."""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from redis import Redis

from goblin_king.contracts import GoblinResult
from goblin_king.kubernetes_artifact_config import ArtifactRetentionRequest
from goblin_king.kubernetes_artifacts import (
    artifact_retention_failure,
    retain_result_artifacts,
)

RESULT_FORWARDER_SCRIPT = r"""
import json
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
        raw_result = result_path.read_text(encoding="utf-8")
        result = json.loads(raw_result)
        if result.get("artifacts"):
            reason = (
                "artifact retention failed: durable Kubernetes artifact storage is not configured"
            )
            previous_error = result.get("error")
            result["status"] = "failed"
            result["error"] = f"{previous_error}; {reason}" if previous_error else reason
            result["artifacts"] = []
            result["metrics"] = {
                key: value
                for key, value in result.get("metrics", {}).items()
                if not key.startswith("artifact.")
            }
            raw_result = json.dumps(result, separators=(",", ":"))
        Redis.from_url(redis_url).set(
            f"goblin-king:results:{run_id}",
            raw_result,
            ex=3600,
        )
        raise SystemExit(0)
    time.sleep(0.25)

raise SystemExit(f"result file not found before timeout: {result_path}")
"""


@dataclass(frozen=True)
class ResultForwarderSettings:
    """Hold the narrow result transport configuration supplied to one sidecar."""

    run_id: str
    redis_url: str
    result_path: Path
    wait_seconds: int = 300

    @classmethod
    def from_environment(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> ResultForwarderSettings:
        """Validate required sidecar settings from the process environment."""
        values = os.environ if environ is None else environ
        wait_seconds = int(values.get("GOBLIN_RESULT_WAIT_SECONDS", "300"))
        if wait_seconds <= 0:
            raise ValueError("GOBLIN_RESULT_WAIT_SECONDS must be positive")
        return cls(
            run_id=values["GOBLIN_RUN_ID"],
            redis_url=values["GOBLIN_REDIS_URL"],
            result_path=Path(values["GOBLIN_RESULT_PATH"]),
            wait_seconds=wait_seconds,
        )


def forward_result(
    settings: ResultForwarderSettings,
    *,
    environ: Mapping[str, str] | None = None,
    redis_factory: Callable[[str], Any] | None = None,
) -> GoblinResult:
    """Wait for a result, retain its artifacts, then publish the final envelope."""
    result = wait_for_result(settings.result_path, settings.wait_seconds)
    try:
        retention = ArtifactRetentionRequest.from_environment(settings.run_id, environ)
    except ValueError as error:
        result = artifact_retention_failure(result, str(error))
    else:
        result = retain_result_artifacts(result, retention)
    factory = redis_factory or Redis.from_url
    factory(settings.redis_url).set(
        f"goblin-king:results:{settings.run_id}",
        result.model_dump_json(),
        ex=3600,
    )
    return result


def wait_for_result(result_path: Path, wait_seconds: int) -> GoblinResult:
    """Wait for a complete, valid result envelope written by the worker."""
    deadline = time.monotonic() + wait_seconds
    last_error: ValueError | OSError | None = None
    while time.monotonic() < deadline:
        if result_path.is_file():
            try:
                return GoblinResult.model_validate_json(result_path.read_text(encoding="utf-8"))
            except (ValueError, OSError) as error:
                last_error = error
        time.sleep(0.25)
    if last_error is not None:
        raise RuntimeError("result file did not become a valid result envelope before timeout")
    raise RuntimeError("result file was not created before timeout")


def main() -> None:
    """Run the result-forwarder process inside a Kubernetes worker Pod."""
    settings = ResultForwarderSettings.from_environment()
    forward_result(settings)


if __name__ == "__main__":
    main()
