"""Local runtime tests for in-process goblin execution."""

from goblin_king.contracts import GoblinContext
from goblin_king.registry import GoblinRegistry
from goblin_king.runtime import InProcessRuntime


def test_echo_goblin_runs_successfully() -> None:
    """Verify a normal GoblinResult-returning goblin completes successfully."""
    registry = GoblinRegistry.from_path("examples/goblins.json")
    definition, entrypoint = registry.resolve("example.echo")
    context = GoblinContext(run_id="run-1", artifact_root=".goblin-king/artifacts/run-1")

    result = InProcessRuntime().run(definition, entrypoint, {"message": "hello"}, context)

    assert result.status == "success"
    assert result.data["echo"] == {"message": "hello"}
    assert result.data["run_id"] == "run-1"


def test_plain_dict_result_is_wrapped() -> None:
    """Verify goblins may return plain dictionaries for ergonomic early authoring."""
    registry = GoblinRegistry.from_path("tests/fixtures/plain-registry.json")
    definition, entrypoint = registry.resolve("example.plain")
    context = GoblinContext(run_id="run-2", artifact_root=".goblin-king/artifacts/run-2")

    result = InProcessRuntime().run(definition, entrypoint, {"value": 2}, context)

    assert result.status == "success"
    assert result.data == {"plain": {"value": 2}, "run_id": "run-2"}


def test_exceptions_become_failed_results() -> None:
    """Verify goblin exceptions are returned as structured failed envelopes."""
    registry = GoblinRegistry.from_path("tests/fixtures/failing-registry.json")
    definition, entrypoint = registry.resolve("example.fail")
    context = GoblinContext(run_id="run-3", artifact_root=".goblin-king/artifacts/run-3")

    result = InProcessRuntime().run(definition, entrypoint, {}, context)

    assert result.status == "failed"
    assert "intentional failure" in (result.error or "")
