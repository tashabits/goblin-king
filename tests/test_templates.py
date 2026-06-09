"""Tests for reusable goblin package template generation."""

from __future__ import annotations

import json
from pathlib import Path

from goblin_king.templates import init_package


def test_init_package_creates_reusable_package_skeleton(tmp_path: Path) -> None:
    """Verify package generation writes Python, registry, image, test, and worker files."""
    target = init_package(
        tmp_path / "generated",
        kind="sample.echo",
        package_name="sample_echo",
        image="sample-echo:local",
    )

    assert (target / "pyproject.toml").exists()
    assert (target / "sample_echo" / "goblin.py").exists()
    assert (target / "tests" / "test_goblin.py").exists()
    assert (target / "workers" / "sample.echo" / "Dockerfile").exists()
    registry = json.loads((target / "goblins.json").read_text(encoding="utf-8"))
    images = json.loads((target / "goblin-images.json").read_text(encoding="utf-8"))
    assert registry["goblins"][0]["kind"] == "sample.echo"
    assert images["workers"]["sample.echo"]["image"] == "sample-echo:local"
    assert "goblin_king.goblins" in (target / "pyproject.toml").read_text(encoding="utf-8")
