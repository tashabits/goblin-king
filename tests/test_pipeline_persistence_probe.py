"""Ensure runtime proof detects lost state instead of silently seeding a replacement."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from examples.pipeline.persistence_probe import probe


def test_probe_replay_and_independent_backup(tmp_path: Path) -> None:
    source_path = tmp_path / "source.sqlite3"
    original = probe(source_path)
    assert original["created"] is True
    assert probe(source_path, original["build_id"])["created"] is False
    restored_path = tmp_path / "restored.sqlite3"
    with (
        closing(sqlite3.connect(source_path)) as source,
        closing(sqlite3.connect(restored_path)) as restored,
    ):
        source.backup(restored)
    source_path.unlink()
    result = probe(restored_path, original["build_id"])
    assert result["build_id"] == original["build_id"]
    assert result["created"] is False
    assert result["integrity_check"] == "ok"
    assert result["scheduler_jobs"] == 0


def test_probe_fails_when_expected_state_is_missing(tmp_path: Path) -> None:
    original = probe(tmp_path / "original.sqlite3")
    with pytest.raises(RuntimeError, match="missing or changed"):
        probe(tmp_path / "lost.sqlite3", original["build_id"])


def test_probe_fails_when_database_was_replaced(tmp_path: Path) -> None:
    original = probe(tmp_path / "original.sqlite3")
    replacement = tmp_path / "replacement.sqlite3"
    probe(replacement)
    with pytest.raises(RuntimeError, match="missing or changed"):
        probe(replacement, original["build_id"])
