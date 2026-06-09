"""Tests for Python package entry point goblin discovery."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from goblin_king.contracts import GoblinDefinition
from goblin_king.registry import GoblinRegistry, RegistryError, _definition_from_entry_point_value


def test_entry_point_value_accepts_definition_dict_and_factory() -> None:
    """Verify supported entry point value shapes normalize to GoblinDefinition."""
    direct = GoblinDefinition(kind="direct.goblin", display_name="Direct", module="examples")
    from_dict = {
        "kind": "dict.goblin",
        "display_name": "Dict",
        "module": "examples",
    }

    assert _definition_from_entry_point_value("direct", direct).kind == "direct.goblin"
    assert _definition_from_entry_point_value("dict", from_dict).kind == "dict.goblin"
    assert (
        _definition_from_entry_point_value("factory", lambda: from_dict).kind == "dict.goblin"
    )


def test_entry_point_value_rejects_unsupported_shape() -> None:
    """Verify unsupported entry point values fail clearly."""
    with pytest.raises(RegistryError, match="must be a GoblinDefinition"):
        _definition_from_entry_point_value("bad", object())


def test_registry_rejects_duplicates_across_files_and_definitions(tmp_path: Path) -> None:
    """Verify duplicate kinds across JSON and entry point definitions are rejected."""
    registry_path = tmp_path / "goblins.json"
    registry_path.write_text(
        json.dumps(
            {
                "goblins": [
                    {
                        "kind": "example.echo",
                        "display_name": "Echo",
                        "module": "examples.goblins.echo",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(RegistryError, match="duplicate goblin kind"):
        GoblinRegistry.from_paths_and_definitions(
            [registry_path],
            [GoblinDefinition(kind="example.echo", display_name="Other", module="examples")],
        )
