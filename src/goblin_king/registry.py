"""JSON-backed goblin registry loading and entrypoint resolution."""

from __future__ import annotations

import importlib
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from goblin_king.contracts import GoblinDefinition


class RegistryError(ValueError):
    """Raised when registry content cannot safely produce executable goblins."""


class RegistryDocument(BaseModel):
    """Validate the top-level JSON registry shape used in Phase 1."""

    goblins: list[GoblinDefinition] = Field(default_factory=list)


class GoblinRegistry:
    """Load goblin definitions and resolve their Python entrypoints by kind."""

    def __init__(
        self,
        definitions: list[GoblinDefinition],
        import_roots: list[Path] | None = None,
    ) -> None:
        self._definitions = self._index_definitions(definitions)
        self._import_roots = import_roots or []

    @classmethod
    def from_path(cls, path: str | Path) -> GoblinRegistry:
        """Read and validate a registry JSON file from disk."""
        definitions, import_root = _read_registry_file(path)
        return cls(
            definitions,
            import_roots=[Path.cwd().resolve(), import_root],
        )

    @classmethod
    def from_paths(cls, paths: list[str | Path]) -> GoblinRegistry:
        """Read, merge, and validate multiple registry JSON files."""
        definitions: list[GoblinDefinition] = []
        import_roots = [Path.cwd().resolve()]
        for path in paths:
            file_definitions, import_root = _read_registry_file(path)
            definitions.extend(file_definitions)
            import_roots.append(import_root)
        return cls(definitions, import_roots=import_roots)

    @classmethod
    def from_definitions(
        cls,
        definitions: list[GoblinDefinition],
        import_roots: list[Path] | None = None,
    ) -> GoblinRegistry:
        """Build a registry from already-discovered definitions."""
        return cls(definitions, import_roots=import_roots)

    @classmethod
    def from_paths_and_definitions(
        cls,
        paths: list[str | Path],
        definitions: list[GoblinDefinition],
    ) -> GoblinRegistry:
        """Merge registry files with externally discovered definitions."""
        registry = cls.from_paths(paths)
        return cls(
            [*registry.list(), *definitions],
            import_roots=registry._import_roots,
        )

    @staticmethod
    def load_file(path: str | Path) -> list[GoblinDefinition]:
        """Read one registry JSON file and return its goblin definitions."""
        definitions, _ = _read_registry_file(path)
        return definitions

    @property
    def import_roots(self) -> list[Path]:
        """Return import roots used for resolving Python entrypoints."""
        return list(self._import_roots)

    @staticmethod
    def _index_definitions(definitions: list[GoblinDefinition]) -> dict[str, GoblinDefinition]:
        """Build the kind lookup while rejecting duplicate registry entries."""
        indexed: dict[str, GoblinDefinition] = {}
        for definition in definitions:
            if definition.kind in indexed:
                raise RegistryError(f"duplicate goblin kind: {definition.kind}")
            indexed[definition.kind] = definition
        return indexed

    def list(self) -> list[GoblinDefinition]:
        """Return registered goblins sorted by kind for stable CLI and test output."""
        return [self._definitions[kind] for kind in sorted(self._definitions)]

    def get(self, kind: str) -> GoblinDefinition:
        """Return one goblin definition or raise a clear error listing available kinds."""
        try:
            return self._definitions[kind]
        except KeyError as error:
            available = ", ".join(sorted(self._definitions)) or "<none>"
            raise RegistryError(f"unknown goblin kind {kind!r}; available: {available}") from error

    def resolve(self, kind: str) -> tuple[GoblinDefinition, Callable[[dict[str, Any], Any], Any]]:
        """Import the registered module and return its callable entrypoint."""
        definition = self.get(kind)
        self._install_import_roots()
        try:
            module = importlib.import_module(definition.module)
        except ImportError as error:
            raise RegistryError(
                f"could not import module {definition.module!r} for goblin {kind!r}"
            ) from error

        entrypoint = getattr(module, definition.entrypoint, None)
        if entrypoint is None:
            raise RegistryError(
                f"module {definition.module!r} has no entrypoint {definition.entrypoint!r}"
            )
        if not callable(entrypoint):
            raise RegistryError(
                f"entrypoint {definition.module}.{definition.entrypoint} is not callable"
            )
        return definition, entrypoint

    def _install_import_roots(self) -> None:
        """Add registry-local import roots so CLI runs can load project goblin modules."""
        for root in reversed(self._import_roots):
            root_text = str(root)
            if root_text not in sys.path:
                sys.path.insert(0, root_text)


def _read_registry_file(path: str | Path) -> tuple[list[GoblinDefinition], Path]:
    """Read and validate one registry file, returning definitions and import root."""
    registry_path = Path(path)
    try:
        payload = json.loads(registry_path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise RegistryError(f"registry not found: {registry_path}") from error
    except json.JSONDecodeError as error:
        raise RegistryError(f"registry is not valid JSON: {registry_path}") from error

    try:
        document = RegistryDocument.model_validate(payload)
    except ValidationError as error:
        raise RegistryError(str(error)) from error
    return document.goblins, registry_path.resolve().parent
