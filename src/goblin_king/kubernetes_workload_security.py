"""Typed, opt-in security policy for generated Kubernetes worker Pods."""

from __future__ import annotations

import re
from typing import Annotated, Literal

from kubernetes.utils.quantity import parse_quantity
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from goblin_king.kubernetes_service_account import (
    KubernetesWorkloadSecurityError,
    attach_worker_service_account_token,
)

KubernetesWorkloadSecurityProfile = Literal["legacy", "restricted-v1"]
KubernetesQuantity = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
_KUBERNETES_OBJECT_NAME = re.compile(r"^[a-z0-9](?:[-a-z0-9.]*[a-z0-9])?$")


def kubernetes_object_name(value: object, *, field_name: str) -> str:
    """Return one validated Kubernetes DNS subdomain name."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    normalized = value.strip()
    if len(normalized) > 253 or _KUBERNETES_OBJECT_NAME.fullmatch(normalized) is None:
        raise ValueError(f"{field_name} must be a Kubernetes DNS subdomain name")
    return normalized


class KubernetesContainerResources(BaseModel):
    """Complete CPU and memory bounds for one restricted workload container."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    cpu_request: KubernetesQuantity
    cpu_limit: KubernetesQuantity
    memory_request: KubernetesQuantity
    memory_limit: KubernetesQuantity

    @field_validator("cpu_request", "cpu_limit", "memory_request", "memory_limit")
    @classmethod
    def validate_quantity(cls, value: str) -> str:
        try:
            parsed = parse_quantity(value)
        except (TypeError, ValueError) as error:
            raise ValueError(f"invalid Kubernetes resource quantity: {value}") from error
        if parsed <= 0:
            raise ValueError("Kubernetes resource quantities must be greater than zero")
        return value

    @model_validator(mode="after")
    def validate_requests_within_limits(self) -> KubernetesContainerResources:
        if parse_quantity(self.cpu_request) > parse_quantity(self.cpu_limit):
            raise ValueError("CPU request must not exceed CPU limit")
        if parse_quantity(self.memory_request) > parse_quantity(self.memory_limit):
            raise ValueError("memory request must not exceed memory limit")
        return self

    def manifest(self) -> dict[str, dict[str, str]]:
        """Return the Kubernetes resources fragment for one container."""
        return {
            "requests": {"cpu": self.cpu_request, "memory": self.memory_request},
            "limits": {"cpu": self.cpu_limit, "memory": self.memory_limit},
        }


def default_worker_resources() -> KubernetesContainerResources:
    return KubernetesContainerResources(
        cpu_request="100m",
        cpu_limit="1",
        memory_request="64Mi",
        memory_limit="512Mi",
    )


def default_forwarder_resources() -> KubernetesContainerResources:
    return KubernetesContainerResources(
        cpu_request="10m",
        cpu_limit="100m",
        memory_request="64Mi",
        memory_limit="128Mi",
    )


class KubernetesRestrictedWorkloadSettings(BaseModel):
    """Versioned restricted profile inputs without arbitrary Pod fragments."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_as_user: int = Field(default=65532, ge=1)
    run_as_group: int = Field(default=65532, ge=1)
    fs_group: int = Field(default=65532, ge=1)
    worker_resources: KubernetesContainerResources = Field(
        default_factory=default_worker_resources
    )
    result_forwarder_resources: KubernetesContainerResources = Field(
        default_factory=default_forwarder_resources
    )
    worker_service_account_names: dict[str, str] = Field(default_factory=dict)

    @field_validator("worker_service_account_names", mode="before")
    @classmethod
    def validate_service_accounts(cls, value: object) -> dict[str, str]:
        """Accept only explicit kind-to-ServiceAccount-name bindings."""
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError("worker_service_account_names must be an object")
        bindings: dict[str, str] = {}
        for kind, name in value.items():
            if not isinstance(kind, str) or not kind.strip():
                raise ValueError("worker service account kinds must be non-empty strings")
            bindings[kind.strip()] = kubernetes_object_name(
                name,
                field_name=f"worker service account for {kind}",
            )
        return dict(sorted(bindings.items()))

    def service_account_for(self, kind: str | None) -> str | None:
        if kind is None:
            return None
        return self.worker_service_account_names.get(kind)

    def pod_security_context(self) -> dict[str, object]:
        return {
            "runAsNonRoot": True,
            "runAsUser": self.run_as_user,
            "runAsGroup": self.run_as_group,
            "fsGroup": self.fs_group,
            "fsGroupChangePolicy": "OnRootMismatch",
            "seccompProfile": {"type": "RuntimeDefault"},
        }

    def container_security_context(self) -> dict[str, object]:
        return {
            "allowPrivilegeEscalation": False,
            "capabilities": {"drop": ["ALL"]},
            "privileged": False,
            "readOnlyRootFilesystem": True,
            "runAsNonRoot": True,
            "runAsUser": self.run_as_user,
            "runAsGroup": self.run_as_group,
            "seccompProfile": {"type": "RuntimeDefault"},
        }


def apply_restricted_workload_security(
    *,
    pod_spec: dict[str, object],
    worker_container: dict[str, object],
    result_forwarder_container: dict[str, object],
    settings: KubernetesRestrictedWorkloadSettings,
    kind: str | None,
    resource_policy_read_only_root: bool | None,
) -> None:
    """Apply the complete restricted-v1 contract after resource-policy translation."""
    if resource_policy_read_only_root is False:
        raise KubernetesWorkloadSecurityError(
            "restricted-v1 rejects filesystem.read_only_root=false"
        )

    service_account_name = settings.service_account_for(kind)
    pod_spec["automountServiceAccountToken"] = False
    if service_account_name is not None:
        pod_spec["serviceAccountName"] = service_account_name
        attach_worker_service_account_token(pod_spec, worker_container)
    pod_spec["securityContext"] = settings.pod_security_context()

    worker_container["securityContext"] = settings.container_security_context()
    worker_container["resources"] = _validated_merged_resources(
        defaults=settings.worker_resources,
        overrides=worker_container.get("resources"),
    )
    result_forwarder_container["securityContext"] = (
        settings.container_security_context()
    )
    result_forwarder_container["resources"] = (
        settings.result_forwarder_resources.manifest()
    )


def _validated_merged_resources(
    *,
    defaults: KubernetesContainerResources,
    overrides: object,
) -> dict[str, dict[str, str]]:
    merged = {
        category: dict(values)
        for category, values in defaults.manifest().items()
    }
    if not isinstance(overrides, dict):
        return merged
    for category in ("requests", "limits"):
        values = overrides.get(category)
        if isinstance(values, dict):
            merged[category].update(
                {str(key): str(value) for key, value in values.items()}
            )
    return KubernetesContainerResources(
        cpu_request=merged["requests"]["cpu"],
        cpu_limit=merged["limits"]["cpu"],
        memory_request=merged["requests"]["memory"],
        memory_limit=merged["limits"]["memory"],
    ).manifest()
