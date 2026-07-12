"""Generic registry-worker contract validation through Kubernetes Jobs."""

from pathlib import Path

from goblin_king.contracts import ArtifactRecord, GoblinDefinition, GoblinResult
from goblin_king.kubernetes_pod_diagnostics import KubernetesRunObservation
from goblin_king.kubernetes_runtime_settings import KubernetesRuntimeSettings
from goblin_king.kubernetes_validation import validate_workers_with_kubernetes
from goblin_king.registry import GoblinRegistry
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


def test_kubernetes_validation_returns_exact_identity_logs_and_artifacts(monkeypatch) -> None:
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
                job_created=True,
                result_received=True,
                result_envelope_valid=True,
                exit_code=0,
                logs={"worker": "validated", "result-forwarder": "forwarded"},
            )

    monkeypatch.setattr(
        "goblin_king.kubernetes_validation.build_kubernetes_runtime",
        lambda **_kwargs: FakeRuntime(),
    )
    result = validate_workers_with_kubernetes(
        registry=registry,
        workers=workers,
        input_payload={"value": 7},
        kinds=["example.generic"],
        require_success=True,
        timeout_seconds=19,
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


def test_kubernetes_validation_rejects_invalid_envelope_even_without_success_gate(
    monkeypatch,
) -> None:
    """Verify contract-invalid output never becomes passing proof."""
    registry, workers = _registry_and_workers()

    class FakeRuntime:
        def run_observed(self, *_args, **_kwargs):
            return KubernetesRunObservation(
                result=GoblinResult.failed(error="worker produced invalid result JSON"),
                job_created=True,
                result_received=True,
                result_envelope_valid=False,
                logs={"worker": "bad output"},
            )

    monkeypatch.setattr(
        "goblin_king.kubernetes_validation.build_kubernetes_runtime",
        lambda **_kwargs: FakeRuntime(),
    )
    result = validate_workers_with_kubernetes(
        registry=registry,
        workers=workers,
        input_payload={},
        kinds=["example.generic"],
        require_success=False,
    )[0]

    assert result.ok is False
    assert result.error == "worker produced invalid result JSON"
    assert result.logs == {"worker": "bad output"}


def test_restricted_validation_uses_per_kind_scheduler_identity(monkeypatch) -> None:
    """Verify generic proof binds the restricted profile and per-kind ServiceAccount."""
    registry, workers = _registry_and_workers()
    captured: dict[str, object] = {}
    settings = KubernetesRuntimeSettings.model_validate(
        {
            "workload_security_profile": "restricted-v1",
            "restricted_workload": {
                "worker_service_account_names": {
                    "example.generic": "goblin-generic-reader"
                }
            },
        }
    )

    class FakeRuntime:
        def run_observed(self, *_args, **_kwargs):
            return KubernetesRunObservation(
                result=GoblinResult.ok(data={"ok": True}),
                job_created=True,
                result_received=True,
                result_envelope_valid=True,
                exit_code=0,
            )

    def fake_build_kubernetes_runtime(**kwargs):
        captured.update(kwargs)
        return FakeRuntime()

    monkeypatch.setattr(
        "goblin_king.kubernetes_validation.build_kubernetes_runtime",
        fake_build_kubernetes_runtime,
    )
    result = validate_workers_with_kubernetes(
        registry=registry,
        workers=workers,
        input_payload={},
        kinds=["example.generic"],
        kubernetes_runtime_settings=settings,
    )[0]

    expected = settings.validation_image_identity(
        "registry.example/generic@sha256:abc",
        "example.generic",
    )
    assert captured["settings"] is settings
    assert result.image_digest == expected
    assert result.image_digest != kubernetes_image_identity(
        "registry.example/generic@sha256:abc"
    )
    assert settings.effective_workload_security("example.generic")[
        "service_account_name"
    ] == "goblin-generic-reader"


def test_kubernetes_validation_does_not_claim_an_uncreated_job(monkeypatch) -> None:
    """Verify setup failures keep the completed-check list truthful."""
    registry, workers = _registry_and_workers()

    class FakeRuntime:
        def run_observed(self, *_args, **_kwargs):
            return KubernetesRunObservation(
                result=GoblinResult.failed(error="kubernetes runtime unavailable"),
            )

    monkeypatch.setattr(
        "goblin_king.kubernetes_validation.build_kubernetes_runtime",
        lambda **_kwargs: FakeRuntime(),
    )
    result = validate_workers_with_kubernetes(
        registry=registry,
        workers=workers,
        input_payload={},
        kinds=["example.generic"],
    )[0]

    assert result.ok is False
    assert result.checks == []
    assert result.error == "kubernetes runtime unavailable"


def test_kubernetes_validation_reports_unknown_registry_kind() -> None:
    """Verify selection failures are returned without contacting Kubernetes."""
    registry, workers = _registry_and_workers()

    results = validate_workers_with_kubernetes(
        registry=registry,
        workers=workers,
        input_payload={},
        kinds=["example.missing"],
    )

    assert len(results) == 1
    assert results[0].ok is False
    assert results[0].error == "unknown goblin kind: example.missing"
