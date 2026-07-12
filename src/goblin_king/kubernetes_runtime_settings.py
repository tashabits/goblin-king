"""Typed operator settings for dynamically generated Kubernetes worker Pods."""

from __future__ import annotations

import json
from collections.abc import Sequence
from hashlib import sha256
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from goblin_king.kubernetes_artifact_config import KubernetesArtifactRetention
from goblin_king.kubernetes_workload_security import (
    KubernetesRestrictedWorkloadSettings,
    KubernetesWorkloadSecurityProfile,
    kubernetes_object_name,
)

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
    workload_security_profile: KubernetesWorkloadSecurityProfile = "legacy"
    restricted_workload: KubernetesRestrictedWorkloadSettings = Field(
        default_factory=KubernetesRestrictedWorkloadSettings
    )
    artifact_retention: KubernetesArtifactRetention | None = Field(
        default_factory=KubernetesArtifactRetention.from_environment
    )

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
            normalized = kubernetes_object_name(
                item,
                field_name="workload image pull Secret name",
            )
            if normalized not in names:
                names.append(normalized)
        return tuple(names)

    def effective_workload_security(self, kind: str | None) -> dict[str, object]:
        """Expose the effective versioned security contract for validation proof."""
        if self.workload_security_profile == "legacy":
            return {"profile": "legacy"}
        service_account_name = self.restricted_workload.service_account_for(kind)
        return {
            "profile": self.workload_security_profile,
            "automount_service_account_token": False,
            "service_account_name": service_account_name,
            "worker_service_account_token_projected": service_account_name is not None,
            "pod_security_context": self.restricted_workload.pod_security_context(),
            "container_security_context": (
                self.restricted_workload.container_security_context()
            ),
            "worker_resources": self.restricted_workload.worker_resources.manifest(),
            "result_forwarder_resources": (
                self.restricted_workload.result_forwarder_resources.manifest()
            ),
        }

    def validation_image_identity(self, image: str, kind: str | None) -> str:
        """Bind restricted validation proof to its effective security contract."""
        base = f"kubernetes:{image}"
        if self.workload_security_profile == "legacy":
            return base
        payload = json.dumps(
            self.effective_workload_security(kind),
            sort_keys=True,
            separators=(",", ":"),
        )
        return f"{base}:workload-security:{sha256(payload.encode()).hexdigest()}"

    @classmethod
    def from_legacy_options(
        cls,
        *,
        result_forwarder_image: str,
        image_pull_policy: str,
        result_forwarder_image_pull_policy: str | None = None,
        workload_image_pull_secret_names: Sequence[str] = (),
        artifact_retention: KubernetesArtifactRetention | None = None,
    ) -> KubernetesRuntimeSettings:
        """Translate the compatibility constructor surface into the typed boundary."""
        values: dict[str, object] = {
            "result_forwarder_image": result_forwarder_image,
            "worker_image_pull_policy": image_pull_policy,
            "result_forwarder_image_pull_policy": (
                result_forwarder_image_pull_policy or image_pull_policy
            ),
            "workload_image_pull_secret_names": workload_image_pull_secret_names,
        }
        if artifact_retention is not None:
            values["artifact_retention"] = artifact_retention
        return cls.model_validate(values)
