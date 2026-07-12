"""Generic registry-worker contract validation through Kubernetes Jobs."""

from pathlib import Path

from goblin_king.contracts import ArtifactRecord, GoblinDefinition, GoblinResult
from goblin_king.kubernetes_validation import validate_workers_with_kubernetes
from goblin_king.registry import GoblinRegistry
from goblin_king.runtime_observation import KubernetesRunObservation
from goblin_king.validation import kubernetes_image_identity
from goblin_king.workers import WorkerImageDefinition, WorkerImageMap


def _registry_and_workers() -> tuple[GoblinRegistry, WorkerImageMap]:
    kind = "example.generic"
    return (
        GoblinRegistry.from_definitions(
            [
                GoblinDefinition(
                    kind=kind,
                    display_name="Generic Worker",
                    module="container.only",
                )
            ]
        ),
        WorkerImageMap.from_definitions(
            {
                kind: WorkerImageDefinition(
                    context=Path("."),
                    image="registry.example/generic@sha256:abc",
                )
            }
        ),
    )


def test_kubernetes_validation_returns_exact_identity_logs_and_artifacts() -> None:
    """Verify a valid Job result produces scheduler-compatible proof diagnostics."""
    registry, workers = _registry_and_workers()
    captured: dict[str, object] = {}

    class FakeRuntime:
        def run_observed(
            self,
            definition,
            _entrypoint,
            input_payload,
            context,
            *,
            timeout_seconds,
        ):
            captured.update(
                definition=definition,
                input=input_payload,
                context=context,
                timeout_seconds=timeout_seconds,
            )
            return KubernetesRunObservation(
                result=GoblinResult.ok(
                    data={"ok": True},
                    artifacts=[
                        ArtifactRecord(
                            name="proof.txt",
                            uri="artifact://proof.txt",
                            media_type="text/plain",
                        )
                    ],
                ),
                result_received=True,
                result_envelope_valid=True,
                exit_code=0,
                logs={"worker": "validated", "result-forwarder": "forwarded"},
            )

    result = validate_workers_with_kubernetes(
        registry=registry,
        workers=workers,
        input_payload={"value": 7},
        kinds=["example.generic"],
        require_success=True,
        timeout_seconds=19,
        runtime=FakeRuntime(),  # type: ignore[arg-type]
    )[0]

    assert result.ok is True
    assert result.image_digest == kubernetes_image_identity(
        "registry.example/generic@sha256:abc"
    )
    assert result.exit_code == 0
    assert result.logs["worker"] == "validated"
    assert result.artifact_count == 1
    assert result.artifacts[0].name == "proof.txt"
    assert result.checks == ["kubernetes-job", "result-envelope", "artifact-metadata"]
    assert captured["input"] == {"value": 7}
    assert captured["timeout_seconds"] == 19


def test_kubernetes_validation_rejects_invalid_envelope_even_without_success_gate() -> None:
    """Verify contract-invalid output never becomes passing proof."""
    registry, workers = _registry_and_workers()

    class FakeRuntime:
        def run_observed(self, *_args, **_kwargs):
            return KubernetesRunObservation(
                result=GoblinResult.failed(error="worker produced invalid result JSON"),
                result_received=True,
                result_envelope_valid=False,
                logs={"worker": "bad output"},
            )

    result = validate_workers_with_kubernetes(
        registry=registry,
        workers=workers,
        input_payload={},
        kinds=["example.generic"],
        require_success=False,
        runtime=FakeRuntime(),  # type: ignore[arg-type]
    )[0]

    assert result.ok is False
    assert result.error == "worker produced invalid result JSON"
    assert result.logs == {"worker": "bad output"}


def test_kubernetes_validation_reports_unknown_registry_kind() -> None:
    """Verify selection failures are returned without contacting Kubernetes."""
    registry, workers = _registry_and_workers()

    results = validate_workers_with_kubernetes(
        registry=registry,
        workers=workers,
        input_payload={},
        kinds=["example.missing"],
        runtime=object(),  # type: ignore[arg-type]
    )

    assert len(results) == 1
    assert results[0].ok is False
    assert results[0].error == "unknown goblin kind: example.missing"
