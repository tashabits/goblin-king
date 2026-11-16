"""Fixed execution profiles for disposable source builds and credential-free tests."""

import re

BUILDKIT_IMAGE = (
    "moby/buildkit@sha256:ef70e34495a1730a7c8e20e98fedd9fab328efd6ecfb37365ac464aa0627eb58"
)


def builder_job(
    *,
    name,
    namespace,
    claim,
    node,
    target,
    dockerfile="Dockerfile",
    context=".",
    labels=None,
    image=BUILDKIT_IMAGE,
    timeout=1800,
):
    """Render the explicit rootless BuildKit profile with no publication authority.

    RootlessKit needs unconfined seccomp/AppArmor and its setuid mapping helpers.
    The normal restricted worker profile is unchanged. This whole sandbox must be
    deleted and its Pods observed absent before a publisher receives the volume.
    """
    if not re.fullmatch(r"[^\s@]+@sha256:[0-9a-f]{64}", image):
        raise ValueError("builder must be pinned by digest")
    if any(part in {"..", ""} for part in dockerfile.split("/")) or dockerfile.startswith("/"):
        raise ValueError("unsafe Dockerfile path")
    if context != ".":
        raise ValueError("this builder profile requires the repository root as context")
    command = [
        "buildctl-daemonless.sh",
        "build",
        "--frontend",
        "dockerfile.v0",
        "--local",
        "context=/scratch/source",
        "--local",
        "dockerfile=/scratch/source",
        "--opt",
        f"filename={dockerfile}",
        "--opt",
        "platform=linux/amd64",
        "--output",
        "type=oci,dest=/scratch/artifact/image.tar",
    ]
    if target:
        command += ["--opt", f"target={target}"]
    for key, value in sorted((labels or {}).items()):
        command += ["--opt", f"label:{key}={value}"]
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "labels": {"goblin-king.io/trust": "source-builder"},
        },
        "spec": {
            "backoffLimit": 0,
            "activeDeadlineSeconds": timeout,
            "template": {
                "metadata": {"labels": {"goblin-king.io/trust": "source-builder"}},
                "spec": {
                    "automountServiceAccountToken": False,
                    "restartPolicy": "Never",
                    "nodeSelector": {"kubernetes.io/hostname": node},
                    "tolerations": [
                        {
                            "key": "node-role.kubernetes.io/control-plane",
                            "operator": "Exists",
                            "effect": "NoSchedule",
                        }
                    ],
                    "securityContext": {"runAsUser": 1000, "runAsGroup": 1000, "fsGroup": 1000},
                    "containers": [
                        {
                            "name": "build",
                            "image": image,
                            "command": command,
                            "env": [
                                {
                                    "name": "BUILDKITD_FLAGS",
                                    "value": (
                                        "--oci-worker-no-process-sandbox "
                                        "--oci-worker-snapshotter=native"
                                    ),
                                }
                            ],
                            "securityContext": {
                                "seccompProfile": {"type": "Unconfined"},
                                "appArmorProfile": {"type": "Unconfined"},
                            },
                            "resources": {
                                "requests": {"cpu": "250m", "memory": "512Mi"},
                                "limits": {"cpu": "2", "memory": "2Gi", "ephemeral-storage": "8Gi"},
                            },
                            "volumeMounts": [
                                {"name": "scratch", "mountPath": "/scratch"},
                                {
                                    "name": "builder-state",
                                    "mountPath": "/home/user/.local/share/buildkit",
                                },
                            ],
                        }
                    ],
                    "volumes": [
                        {"name": "scratch", "persistentVolumeClaim": {"claimName": claim}},
                        {"name": "builder-state", "emptyDir": {"sizeLimit": "8Gi"}},
                    ],
                },
            },
        },
    }


def source_network_policy(namespace):
    """Allow public dependency downloads and DNS while excluding private service networks."""
    return {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": {"name": "source-isolation", "namespace": namespace},
        "spec": {
            "podSelector": {
                "matchExpressions": [
                    {
                        "key": "goblin-king.io/trust",
                        "operator": "In",
                        "values": ["source-builder", "source-test"],
                    }
                ]
            },
            "policyTypes": ["Ingress", "Egress"],
            "ingress": [],
            "egress": [
                {
                    "to": [
                        {
                            "namespaceSelector": {
                                "matchLabels": {"kubernetes.io/metadata.name": "kube-system"}
                            },
                            "podSelector": {"matchLabels": {"k8s-app": "kube-dns"}},
                        }
                    ],
                    "ports": [{"protocol": "UDP", "port": 53}, {"protocol": "TCP", "port": 53}],
                },
                {
                    "to": [
                        {
                            "ipBlock": {
                                "cidr": "0.0.0.0/0",
                                "except": [
                                    "10.0.0.0/8",
                                    "172.16.0.0/12",
                                    "192.168.0.0/16",
                                    "127.0.0.0/8",
                                    "169.254.0.0/16",
                                    "100.64.0.0/10",
                                    "224.0.0.0/4",
                                ],
                            }
                        }
                    ],
                    "ports": [{"protocol": "TCP", "port": 443}, {"protocol": "TCP", "port": 80}],
                },
            ],
        },
    }
