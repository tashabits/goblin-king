"""Shared CLI option declarations for generated Kubernetes workloads."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from goblin_king.jsonio import read_json_file
from goblin_king.kubernetes_runtime_settings import (
    DEFAULT_KUBERNETES_IMAGE_PULL_POLICY,
    DEFAULT_RESULT_FORWARDER_IMAGE,
    KubernetesImagePullPolicy,
    KubernetesRuntimeSettings,
)

ResultForwarderImageOption = Annotated[
    str,
    typer.Option(
        "--result-forwarder-image",
        help="Image reference for the result-forwarder sidecar.",
    ),
]
WorkerImagePullPolicyOption = Annotated[
    KubernetesImagePullPolicy,
    typer.Option(
        "--worker-image-pull-policy",
        help="Kubernetes image pull policy for worker containers.",
    ),
]
ResultForwarderImagePullPolicyOption = Annotated[
    KubernetesImagePullPolicy,
    typer.Option(
        "--result-forwarder-image-pull-policy",
        help="Kubernetes image pull policy for result-forwarder sidecars.",
    ),
]
WorkloadImagePullSecretsOption = Annotated[
    list[str] | None,
    typer.Option(
        "--workload-image-pull-secret",
        help="Existing Kubernetes Secret name for generated worker Pods; repeatable.",
    ),
]
KubernetesRuntimeSettingsPathOption = Annotated[
    Path | None,
    typer.Option(
        "--kubernetes-runtime-settings",
        help="Optional JSON file merged over the Kubernetes runtime CLI settings.",
    ),
]


def kubernetes_runtime_settings(
    *,
    result_forwarder_image: str = DEFAULT_RESULT_FORWARDER_IMAGE,
    worker_image_pull_policy: KubernetesImagePullPolicy = (
        DEFAULT_KUBERNETES_IMAGE_PULL_POLICY
    ),
    result_forwarder_image_pull_policy: KubernetesImagePullPolicy = (
        DEFAULT_KUBERNETES_IMAGE_PULL_POLICY
    ),
    workload_image_pull_secrets: list[str] | None = None,
    settings_path: Path | None = None,
) -> KubernetesRuntimeSettings:
    """Build the validated runtime boundary shared by direct and scheduled commands."""
    base = KubernetesRuntimeSettings(
        result_forwarder_image=result_forwarder_image,
        worker_image_pull_policy=worker_image_pull_policy,
        result_forwarder_image_pull_policy=result_forwarder_image_pull_policy,
        workload_image_pull_secret_names=workload_image_pull_secrets or (),
    )
    if settings_path is None:
        return base
    try:
        overrides = read_json_file(settings_path)
        return KubernetesRuntimeSettings.model_validate(
            {**base.model_dump(mode="json"), **overrides}
        )
    except (OSError, ValueError) as error:
        raise typer.BadParameter(
            f"invalid Kubernetes runtime settings file {settings_path}: {error}",
            param_hint="--kubernetes-runtime-settings",
        ) from error
