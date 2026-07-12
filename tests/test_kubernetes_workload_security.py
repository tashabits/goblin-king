"""Restricted Kubernetes workload security profile tests."""

from __future__ import annotations

import pytest

from goblin_king.contracts import GoblinContext
from goblin_king.kubernetes_runtime_settings import KubernetesRuntimeSettings
from goblin_king.kubernetes_workload_security import (
    KubernetesWorkloadSecurityError,
)
from goblin_king.resource_policies import ResourcePolicy
from goblin_king.runtime import KubernetesRuntime
from goblin_king.workers import WorkerImageDefinition, WorkerImageMap


def _runtime(settings: KubernetesRuntimeSettings) -> KubernetesRuntime:
    return KubernetesRuntime(
        workers=WorkerImageMap(
            {"example.echo": WorkerImageDefinition(context=".", image="echo:local")},
            root=".",
        ),
        namespace="workers",
        settings=settings,
    )


def _manifest(
    settings: KubernetesRuntimeSettings,
    *,
    kind: str = "example.echo",
    resource_policy: ResourcePolicy | None = None,
) -> dict:
    return _runtime(settings)._job_manifest(
        name="gk-example-echo-run-security",
        config_name="gk-example-echo-run-security-input",
        image="registry.example/echo@sha256:" + "a" * 64,
        context=GoblinContext(
            run_id="run-security",
            artifact_root=".goblin-king/artifacts/run-security",
            metadata={"job_id": "job-security", "kind": kind},
        ),
        worker_id="k8s-worker-run-security",
        timeout_seconds=30,
        resource_policy=resource_policy,
        kind=kind,
    )


def test_legacy_profile_preserves_original_manifest_shape() -> None:
    pod_spec = _manifest(KubernetesRuntimeSettings())["spec"]["template"]["spec"]
    worker, forwarder = pod_spec["containers"]

    assert "automountServiceAccountToken" not in pod_spec
    assert "serviceAccountName" not in pod_spec
    assert "securityContext" not in pod_spec
    assert "securityContext" not in worker
    assert "resources" not in worker
    assert "securityContext" not in forwarder
    assert "resources" not in forwarder


def test_restricted_profile_hardens_pod_and_every_container() -> None:
    policy = ResourcePolicy.model_validate(
        {
            "cpu": {"request": "250m"},
            "memory": {"limit": "256Mi"},
            "filesystem": {"read_only_root": True},
        }
    )
    pod_spec = _manifest(
        KubernetesRuntimeSettings(workload_security_profile="restricted-v1"),
        resource_policy=policy,
    )["spec"]["template"]["spec"]

    assert pod_spec["automountServiceAccountToken"] is False
    assert "serviceAccountName" not in pod_spec
    assert pod_spec["securityContext"] == {
        "runAsNonRoot": True,
        "runAsUser": 65532,
        "runAsGroup": 65532,
        "fsGroup": 65532,
        "fsGroupChangePolicy": "OnRootMismatch",
        "seccompProfile": {"type": "RuntimeDefault"},
    }
    assert all("serviceAccountToken" not in str(volume) for volume in pod_spec["volumes"])

    worker, forwarder = pod_spec["containers"]
    expected_security = {
        "allowPrivilegeEscalation": False,
        "capabilities": {"drop": ["ALL"]},
        "privileged": False,
        "readOnlyRootFilesystem": True,
        "runAsNonRoot": True,
        "runAsUser": 65532,
        "runAsGroup": 65532,
        "seccompProfile": {"type": "RuntimeDefault"},
    }
    assert worker["securityContext"] == expected_security
    assert forwarder["securityContext"] == expected_security
    assert worker["resources"] == {
        "requests": {"cpu": "250m", "memory": "64Mi"},
        "limits": {"cpu": "1", "memory": "256Mi"},
    }
    assert forwarder["resources"] == {
        "requests": {"cpu": "10m", "memory": "16Mi"},
        "limits": {"cpu": "100m", "memory": "64Mi"},
    }
    assert forwarder["volumeMounts"] == [
        {"name": "result", "mountPath": "/goblin-result"}
    ]


def test_service_account_token_is_opt_in_for_one_declared_kind() -> None:
    settings = KubernetesRuntimeSettings.model_validate(
        {
            "workload_security_profile": "restricted-v1",
            "restricted_workload": {
                "worker_service_account_names": {
                    "example.cluster-reader": "goblin-cluster-reader"
                }
            },
        }
    )

    generic = _manifest(settings)["spec"]["template"]["spec"]
    declared = _manifest(settings, kind="example.cluster-reader")["spec"]["template"][
        "spec"
    ]

    assert generic["automountServiceAccountToken"] is False
    assert "serviceAccountName" not in generic
    assert declared["automountServiceAccountToken"] is False
    assert declared["serviceAccountName"] == "goblin-cluster-reader"
    worker, forwarder = declared["containers"]
    token_volume = next(
        volume
        for volume in declared["volumes"]
        if volume["name"] == "worker-service-account-token"
    )
    assert token_volume["projected"]["sources"][0]["serviceAccountToken"] == {
        "expirationSeconds": 3600,
        "path": "token",
    }
    assert any(
        mount["name"] == "worker-service-account-token"
        for mount in worker["volumeMounts"]
    )
    assert all(
        mount["name"] != "worker-service-account-token"
        for mount in forwarder["volumeMounts"]
    )


def test_restricted_profile_rejects_read_write_root_relaxation() -> None:
    settings = KubernetesRuntimeSettings(workload_security_profile="restricted-v1")
    policy = ResourcePolicy.model_validate({"filesystem": {"read_only_root": False}})

    with pytest.raises(KubernetesWorkloadSecurityError, match="read_only_root=false"):
        _manifest(settings, resource_policy=policy)


@pytest.mark.parametrize(
    "restricted_workload",
    [
        {"pod_security_context": {"runAsNonRoot": False}},
        {"automount_service_account_token": True},
        {"worker_service_account_names": {"example.echo": "user:password"}},
    ],
)
def test_typed_settings_reject_raw_relaxations_and_credentials(
    restricted_workload: dict,
) -> None:
    with pytest.raises(ValueError):
        KubernetesRuntimeSettings.model_validate(
            {
                "workload_security_profile": "restricted-v1",
                "restricted_workload": restricted_workload,
            }
        )


def test_validation_identity_changes_only_for_restricted_contract() -> None:
    image = "registry.example/echo@sha256:" + "a" * 64
    legacy = KubernetesRuntimeSettings()
    restricted = KubernetesRuntimeSettings(workload_security_profile="restricted-v1")
    privileged_kind = KubernetesRuntimeSettings.model_validate(
        {
            "workload_security_profile": "restricted-v1",
            "restricted_workload": {
                "worker_service_account_names": {"example.echo": "goblin-reader"}
            },
        }
    )

    assert legacy.validation_image_identity(image, "example.echo") == f"kubernetes:{image}"
    assert restricted.validation_image_identity(
        image, "example.echo"
    ) != privileged_kind.validation_image_identity(image, "example.echo")
    assert restricted.validation_image_identity(image, "example.echo").startswith(
        f"kubernetes:{image}:workload-security:"
    )


def test_resource_requests_cannot_exceed_limits() -> None:
    with pytest.raises(ValueError, match="CPU request"):
        KubernetesRuntimeSettings.model_validate(
            {
                "restricted_workload": {
                    "result_forwarder_resources": {
                        "cpu_request": "2",
                        "cpu_limit": "1",
                        "memory_request": "16Mi",
                        "memory_limit": "64Mi",
                    }
                }
            }
        )
