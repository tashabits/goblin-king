"""Typed operator settings for dynamically generated Kubernetes worker Pods."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

KubernetesImagePullPolicy = Literal["Always", "IfNotPresent", "Never"]
DEFAULT_KUBERNETES_IMAGE_PULL_POLICY: KubernetesImagePullPolicy = "IfNotPresent"
DEFAULT_RESULT_FORWARDER_IMAGE = "goblin-king:local"


class KubernetesRuntimeSettings(BaseModel):
    """Constrain operator-controlled image settings without exposing raw Pod fragments."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    result_forwarder_image: str = Field(
        default=DEFAULT_RESULT_FORWARDER_IMAGE,
        min_length=1,
    )
    worker_image_pull_policy: KubernetesImagePullPolicy = DEFAULT_KUBERNETES_IMAGE_PULL_POLICY
    result_forwarder_image_pull_policy: KubernetesImagePullPolicy = (
        DEFAULT_KUBERNETES_IMAGE_PULL_POLICY
    )
    workload_image_pull_secret_names: tuple[str, ...] = ()

    @field_validator("result_forwarder_image")
    @classmethod
    def validate_forwarder_image(cls, value: str) -> str:
        """Reject an empty image reference while leaving OCI parsing to Kubernetes."""
        normalized = value.strip()
        if not normalized:
            raise ValueError("result_forwarder_image must not be empty")
        return normalized

    @field_validator("workload_image_pull_secret_names", mode="before")
    @classmethod
    def validate_pull_secret_names(cls, value: object) -> tuple[str, ...]:
        """Accept only symbolic Secret names and remove duplicates without reordering."""
        if value is None:
            return ()
        if isinstance(value, str) or not isinstance(value, Sequence):
            raise ValueError("workload_image_pull_secret_names must be a list of names")
        names: list[str] = []
        for item in value:
            if not isinstance(item, str) or not item.strip():
                raise ValueError("workload image pull Secret names must be non-empty strings")
            normalized = item.strip()
            if normalized not in names:
                names.append(normalized)
        return tuple(names)

    @classmethod
    def from_legacy_options(
        cls,
        *,
        result_forwarder_image: str,
        image_pull_policy: str,
        result_forwarder_image_pull_policy: str | None = None,
        workload_image_pull_secret_names: Sequence[str] = (),
    ) -> KubernetesRuntimeSettings:
        """Translate the compatibility constructor surface into the typed boundary."""
        return cls.model_validate(
            {
                "result_forwarder_image": result_forwarder_image,
                "worker_image_pull_policy": image_pull_policy,
                "result_forwarder_image_pull_policy": (
                    result_forwarder_image_pull_policy or image_pull_policy
                ),
                "workload_image_pull_secret_names": workload_image_pull_secret_names,
            }
        )
