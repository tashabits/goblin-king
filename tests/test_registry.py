"""Local registry tests for JSON loading and entrypoint validation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from goblin_king.registry import GoblinRegistry, RegistryError


def write_registry(path: Path, goblins: list[dict]) -> Path:
    """Write a small registry fixture and return its path."""
    path.write_text(json.dumps({"goblins": goblins}), encoding="utf-8")
    return path


def test_valid_example_registry_loads() -> None:
    """Verify the committed example registry resolves the echo goblin."""
    registry = GoblinRegistry.from_path("examples/goblins.json")

    assert registry.list()[0].kind == "example.echo"
    definition, entrypoint = registry.resolve("example.echo")
    assert definition.display_name == "Example Echo"
    assert callable(entrypoint)


def test_duplicate_kind_is_rejected(tmp_path: Path) -> None:
    """Verify duplicate kinds fail loudly during registry load."""
    registry_path = write_registry(
        tmp_path / "goblins.json",
        [
            {"kind": "example.echo", "display_name": "One", "module": "examples.goblins.echo"},
            {"kind": "example.echo", "display_name": "Two", "module": "examples.goblins.echo"},
        ],
    )

    with pytest.raises(RegistryError, match="duplicate goblin kind"):
        GoblinRegistry.from_path(registry_path)


def test_missing_module_is_rejected(tmp_path: Path) -> None:
    """Verify unresolved modules produce a registry error when resolved."""
    registry_path = write_registry(
        tmp_path / "goblins.json",
        [{"kind": "example.missing", "display_name": "Missing", "module": "examples.missing"}],
    )

    with pytest.raises(RegistryError, match="could not import module"):
        GoblinRegistry.from_path(registry_path).resolve("example.missing")


def test_missing_entrypoint_is_rejected(tmp_path: Path) -> None:
    """Verify existing modules without the configured entrypoint fail clearly."""
    registry_path = write_registry(
        tmp_path / "goblins.json",
        [
            {
                "kind": "example.nope",
                "display_name": "Nope",
                "module": "examples.goblins.echo",
                "entrypoint": "nope",
            }
        ],
    )

    with pytest.raises(RegistryError, match="has no entrypoint"):
        GoblinRegistry.from_path(registry_path).resolve("example.nope")


def test_non_callable_entrypoint_is_rejected(tmp_path: Path) -> None:
    """Verify non-callable module attributes cannot masquerade as goblins."""
    registry_path = write_registry(
        tmp_path / "goblins.json",
        [
            {
                "kind": "example.not-callable",
                "display_name": "Not Callable",
                "module": "examples.goblins.not_callable",
            }
        ],
    )

    with pytest.raises(RegistryError, match="is not callable"):
        GoblinRegistry.from_path(registry_path).resolve("example.not-callable")
