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
