"""Local tests for Docker worker image configuration."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from goblin_king.workers import WorkerConfigError, WorkerImageMap


def test_worker_image_map_loads_example_worker() -> None:
    """Verify the example worker image map resolves its self-contained build context."""
    worker_map = WorkerImageMap.from_path("goblin-images.json")
    worker = worker_map.get("example.echo")

    assert worker.image == "goblin-king-example-echo:local"
    assert worker.dockerfile == "Dockerfile"
    assert worker_map.resolved_context(worker).name == "example.echo"


def test_worker_image_map_rejects_missing_kind(tmp_path: Path) -> None:
    """Verify Docker runtime setup fails clearly when a worker kind is unmapped."""
    image_map = tmp_path / "goblin-images.json"
    image_map.write_text(json.dumps({"workers": {}}), encoding="utf-8")
    worker_map = WorkerImageMap.from_path(image_map)

    with pytest.raises(WorkerConfigError, match="missing Docker worker image mapping"):
        worker_map.get("example.echo")


def test_worker_image_map_rejects_invalid_json(tmp_path: Path) -> None:
    """Verify malformed image maps fail before Docker commands are attempted."""
    image_map = tmp_path / "goblin-images.json"
    image_map.write_text("{", encoding="utf-8")

    with pytest.raises(WorkerConfigError, match="not valid JSON"):
        WorkerImageMap.from_path(image_map)
