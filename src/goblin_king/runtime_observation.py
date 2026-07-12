"""Bounded diagnostic details captured from container runtime executions."""

from __future__ import annotations

from dataclasses import dataclass, field

from goblin_king.contracts import GoblinResult


@dataclass(frozen=True, slots=True)
class KubernetesRunObservation:
    """Describe one Kubernetes worker result plus bounded pod diagnostics."""

    result: GoblinResult
    result_received: bool = False
    result_envelope_valid: bool = False
    exit_code: int | None = None
    logs: dict[str, str] = field(default_factory=dict)

    def with_runtime_diagnostics(
        self,
        *,
        exit_code: int | None,
        logs: dict[str, str],
    ) -> KubernetesRunObservation:
        """Return a copy enriched with diagnostics captured before pod cleanup."""
        return KubernetesRunObservation(
            result=self.result,
            result_received=self.result_received,
            result_envelope_valid=self.result_envelope_valid,
            exit_code=exit_code,
            logs=logs,
        )
