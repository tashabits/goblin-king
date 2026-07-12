"""Narrow ServiceAccount token projection for opted-in Kubernetes workers."""

from __future__ import annotations


class KubernetesWorkloadSecurityError(ValueError):
    """Raised when a job requests a relaxation forbidden by its security profile."""


def attach_worker_service_account_token(
    pod_spec: dict[str, object],
    worker_container: dict[str, object],
) -> None:
    """Project one bounded token into the worker container and nowhere else."""
    volumes = pod_spec.get("volumes")
    volume_mounts = worker_container.get("volumeMounts")
    if not isinstance(volumes, list) or not isinstance(volume_mounts, list):
        raise KubernetesWorkloadSecurityError(
            "worker service account projection requires manifest volume lists"
        )
    volumes.append(
        {
            "name": "worker-service-account-token",
            "projected": {
                "defaultMode": 420,
                "sources": [
                    {
                        "serviceAccountToken": {
                            "expirationSeconds": 3600,
                            "path": "token",
                        }
                    },
                    {
                        "configMap": {
                            "name": "kube-root-ca.crt",
                            "items": [{"key": "ca.crt", "path": "ca.crt"}],
                        }
                    },
                    {
                        "downwardAPI": {
                            "items": [
                                {
                                    "path": "namespace",
                                    "fieldRef": {
                                        "apiVersion": "v1",
                                        "fieldPath": "metadata.namespace",
                                    },
                                }
                            ]
                        }
                    },
                ],
            },
        }
    )
    volume_mounts.append(
        {
            "name": "worker-service-account-token",
            "mountPath": "/var/run/secrets/kubernetes.io/serviceaccount",
            "readOnly": True,
        }
    )
