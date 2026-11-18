"""Prepare the local simulator and isolated delivery namespaces without remote Git access."""

from __future__ import annotations

import argparse
import base64
import json
import re
import secrets
import subprocess
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from goblin_king.pipeline_sandbox import source_network_policy
from goblin_king.pipeline_settings import PipelineSettings

ROOT = Path(__file__).resolve().parents[1]


class DemoPreparation:
    def __init__(self, context, output, images, *, prefix="gk-delivery", acceptance_id=None):
        self.context, self.output, self.images = context, output, images
        output.mkdir(parents=True, exist_ok=True)
        if not re.fullmatch(r"[a-z][a-z0-9-]{1,30}[a-z0-9]", prefix):
            raise ValueError("installation prefix must be a short DNS label")
        self.prefix, self.acceptance_id = prefix, acceptance_id
        self.control = prefix + "-demo"
        self.provider = prefix + "-provider"

    def kube(self, *args, body=None):
        result = subprocess.run(
            ["kubectl", "--context", self.context, *args],
            input=json.dumps(body) if body is not None else None,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=180,
        )
        if result.returncode:
            raise RuntimeError(
                f"kubectl {args[0]} failed; no submitted Secret contents were logged"
            )
        return result.stdout

    def apply(self, document):
        self.kube("apply", "-f", "-", body=document)

    def namespace(self, name):
        existing = json.loads(
            self.kube("get", "namespace", name, "--ignore-not-found", "-o", "json") or "null"
        )
        if existing:
            labels = existing["metadata"].get("labels", {})
            if self.acceptance_id and labels.get("goblin-king.io/acceptance") != self.acceptance_id:
                raise RuntimeError("refusing to adopt a namespace from another acceptance run")
            if labels.get("goblin-king.io/local-delivery") != "true" and not (
                name == self.control and labels.get("goblin-king.io/retained-demo") == "true"
            ):
                raise RuntimeError("refusing to adopt a namespace without local delivery ownership")
        self.apply(
            {
                "apiVersion": "v1",
                "kind": "Namespace",
                "metadata": {
                    "name": name,
                    "labels": {
                        "goblin-king.io/local-delivery": "true",
                        **(
                            {"goblin-king.io/acceptance": self.acceptance_id}
                            if self.acceptance_id
                            else {"goblin-king.io/retained-demo": "true"}
                        ),
                    },
                },
            }
        )

    def secret(self, namespace, name, data, *, secret_type="Opaque"):
        existing = self.kube(
            "-n", namespace, "get", "secret", name, "--ignore-not-found", "-o", "json"
        )
        if existing:
            return {
                key: base64.b64decode(value) for key, value in json.loads(existing)["data"].items()
            }
        encoded = {
            key: base64.b64encode(value.encode() if isinstance(value, str) else value).decode()
            for key, value in data.items()
        }
        self.kube(
            "create",
            "-f",
            "-",
            body={
                "apiVersion": "v1",
                "kind": "Secret",
                "type": secret_type,
                "metadata": {"name": name, "namespace": namespace},
                "data": encoded,
            },
        )
        return {key: base64.b64decode(value) for key, value in encoded.items()}

    def source_secret(self, name):
        data = json.loads(
            self.kube("-n", "gk-local-registry", "get", "secret", name, "-o", "json")
        )["data"]
        return {key: base64.b64decode(value) for key, value in data.items()}

    def prepare(self):
        self.namespace(self.control)
        self.namespace(self.provider)
        # Docker Desktop makes local tags available to nodes. Cache and inspect these
        # installation images before any credentialed component uses their digests.
        self.apply(
            {
                "apiVersion": "apps/v1",
                "kind": "DaemonSet",
                "metadata": {"name": "pipeline-image-cache", "namespace": self.control},
                "spec": {
                    "selector": {"matchLabels": {"app": "pipeline-image-cache"}},
                    "template": {
                        "metadata": {
                            "labels": {"app": "pipeline-image-cache"},
                            "annotations": {
                                "goblin-king.io/images": json.dumps(self.images, sort_keys=True)
                            },
                        },
                        "spec": {
                            "automountServiceAccountToken": False,
                            "tolerations": [{"operator": "Exists"}],
                            "containers": [
                                {
                                    "name": name,
                                    "image": image["tag"],
                                    "imagePullPolicy": "Always",
                                    "command": ["sh", "-c", "sleep 3600"],
                                    "resources": {
                                        "requests": {"cpu": "5m", "memory": "16Mi"},
                                        "limits": {"cpu": "100m", "memory": "64Mi"},
                                    },
                                }
                                for name, image in self.images.items()
                            ],
                        },
                    },
                },
            }
        )
        self.kube(
            "-n",
            self.control,
            "rollout",
            "status",
            "daemonset/pipeline-image-cache",
            "--timeout=120s",
        )
        cache = json.loads(
            self.kube(
                "-n", self.control, "get", "pods", "-l", "app=pipeline-image-cache", "-o", "json"
            )
        )
        for pod in cache["items"]:
            for container in pod["status"]["containerStatuses"]:
                expected = self.images[container["name"]]["digest"].split("@", 1)[1]
                if not container["imageID"].endswith(expected):
                    raise RuntimeError(
                        "cached local image does not match the pinned installation digest"
                    )
        (self.output / "image-cache.json").write_text(json.dumps(cache, indent=2))
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        github = self.secret(
            self.control,
            "pipeline-github-app",
            {
                "private-key.pem": key.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption(),
                ),
                "webhook-secret": secrets.token_urlsafe(32),
                "fixture-token": secrets.token_urlsafe(32),
                "token-signing-secret": secrets.token_urlsafe(32),
                "app-id": "1",
            },
        )
        self.secret(self.provider, "simulator-credentials", github)
        password = secrets.token_urlsafe(32)
        auth = self.secret(
            self.control,
            "pipeline-auth",
            {
                "api-token": secrets.token_urlsafe(32),
                "redis-password": password,
                "redis-url": f"redis://:{password}@delivery-redis:6379/0",
            },
        )
        (self.output / "admin-token").write_bytes(auth["api-token"])
        (self.output / "fixture-token").write_bytes(github["fixture-token"])
        self.apply(
            {
                "apiVersion": "v1",
                "kind": "PersistentVolumeClaim",
                "metadata": {"name": "provider-data", "namespace": self.provider},
                "spec": {
                    "accessModes": ["ReadWriteOnce"],
                    "resources": {"requests": {"storage": "2Gi"}},
                },
            }
        )
        self.apply(
            {
                "apiVersion": "apps/v1",
                "kind": "Deployment",
                "metadata": {"name": "provider", "namespace": self.provider},
                "spec": {
                    "replicas": 1,
                    "strategy": {"type": "Recreate"},
                    "selector": {"matchLabels": {"app": "pipeline-provider"}},
                    "template": {
                        "metadata": {"labels": {"app": "pipeline-provider"}},
                        "spec": {
                            "automountServiceAccountToken": False,
                            "securityContext": {
                                "runAsUser": 1000,
                                "runAsGroup": 1000,
                                "fsGroup": 1000,
                            },
                            "containers": [
                                {
                                    "name": "provider",
                                    "image": self.images["provider"]["digest"],
                                    "args": [
                                        "--state",
                                        "/data",
                                        "--host",
                                        "0.0.0.0",
                                        "--port",
                                        "8770",
                                    ],
                                    "ports": [{"containerPort": 8770}],
                                    "readinessProbe": {
                                        "httpGet": {"path": "/health", "port": 8770},
                                        "periodSeconds": 2,
                                    },
                                    "resources": {
                                        "requests": {"cpu": "100m", "memory": "128Mi"},
                                        "limits": {"cpu": "1", "memory": "512Mi"},
                                    },
                                    "volumeMounts": [{"name": "data", "mountPath": "/data"}]
                                    + [
                                        {
                                            "name": "credentials",
                                            "mountPath": "/data/" + key,
                                            "subPath": key,
                                            "readOnly": True,
                                        }
                                        for key in github
                                    ],
                                }
                            ],
                            "volumes": [
                                {
                                    "name": "data",
                                    "persistentVolumeClaim": {"claimName": "provider-data"},
                                },
                                {
                                    "name": "credentials",
                                    "secret": {"secretName": "simulator-credentials"},
                                },
                            ],
                        },
                    },
                },
            }
        )
        self.apply(
            {
                "apiVersion": "v1",
                "kind": "Service",
                "metadata": {"name": "provider", "namespace": self.provider},
                "spec": {
                    "selector": {"app": "pipeline-provider"},
                    "ports": [{"port": 8770, "targetPort": 8770}],
                },
            }
        )
        pull = self.source_secret("registry-pull")
        authorizations = json.loads(pull[".dockerconfigjson"])["auths"]
        if len(authorizations) != 1:
            raise RuntimeError("local registry pull identity must name exactly one destination")
        registry = next(iter(authorizations))
        service = json.loads(
            self.kube("-n", "gk-local-registry", "get", "service", "registry", "-o", "json")
        )
        nodes = json.loads(self.kube("get", "nodes", "-o", "json"))["items"]
        port = service["spec"]["ports"][0]["nodePort"]
        endpoints = {
            f"{address['address']}:{port}"
            for node in nodes
            for address in node["status"]["addresses"]
            if address["type"] == "InternalIP"
        }
        if registry not in endpoints:
            raise RuntimeError("publication identity does not match the local registry Service")
        pull_auth = authorizations[registry]["auth"]
        username, reader_password = base64.b64decode(pull_auth).decode().split(":", 1)
        ca = self.source_secret("python-publish")["ca.crt"]
        self.secret(
            self.control,
            "registry-reader",
            {"username": username, "password": reader_password, "ca.crt": ca},
        )
        sources = {}
        for index, implementation in enumerate(("python", "node")):
            namespace = f"{self.prefix}-{implementation}-build"
            self.namespace(namespace)
            self.apply(source_network_policy(namespace))
            self.secret(
                namespace,
                "pipeline-github-app",
                {key: github[key] for key in ("private-key.pem", "webhook-secret", "app-id")},
            )
            self.secret(
                namespace, "registry-publisher", self.source_secret(implementation + "-publish")
            )
            self.secret(
                namespace, "registry-pull", pull, secret_type="kubernetes.io/dockerconfigjson"
            )
            self.apply(
                {
                    "apiVersion": "rbac.authorization.k8s.io/v1",
                    "kind": "Role",
                    "metadata": {"name": "pipeline-build", "namespace": namespace},
                    "rules": [
                        {
                            "apiGroups": [""],
                            "resources": ["configmaps", "persistentvolumeclaims"],
                            "verbs": ["create", "get", "list", "update", "delete"],
                        },
                        {
                            "apiGroups": [""],
                            "resources": ["pods", "pods/log"],
                            "verbs": ["get", "list", "watch"],
                        },
                        {
                            "apiGroups": ["batch"],
                            "resources": ["jobs"],
                            "verbs": ["create", "get", "list", "delete"],
                        },
                    ],
                }
            )
            self.apply(
                {
                    "apiVersion": "rbac.authorization.k8s.io/v1",
                    "kind": "RoleBinding",
                    "metadata": {"name": "pipeline-build", "namespace": namespace},
                    "subjects": [
                        {
                            "kind": "ServiceAccount",
                            "name": "pipeline-build",
                            "namespace": self.control,
                        }
                    ],
                    "roleRef": {
                        "apiGroup": "rbac.authorization.k8s.io",
                        "kind": "Role",
                        "name": "pipeline-build",
                    },
                }
            )
            repositories = []
            image_path = (
                implementation
                if self.prefix == "gk-delivery"
                else f"{implementation}/{self.prefix}"
            )
            for environment in ("test", "prod"):
                target = f"{self.prefix}-{implementation}-{environment}"
                self.namespace(target)
                self.secret(
                    target, "registry-pull", pull, secret_type="kubernetes.io/dockerconfigjson"
                )
                repositories.append(f"{registry}/{image_path}/{environment}")
            sources[f"fixture/{implementation}-api"] = {
                "namespace": namespace,
                "node": "desktop-control-plane" if index == 0 else "desktop-worker",
                "image_repositories": repositories,
                "staging_repository": f"{image_path}/staging",
                "github_secret": "pipeline-github-app",
                "publisher_secret": "registry-publisher",
                "pull_secret": "registry-pull",
            }
        revision = (
            self.images["controller"].get("source_revision")
            or subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        )
        provider = {
            "app_id": 1,
            "installation_id": 1,
            "simulated_provider": True,
            "provider_url": f"http://provider.{self.provider}.svc:8770",
        }
        policy = PipelineSettings().worker_policy.compact()
        values = {
            "fullnameOverride": "delivery",
            "image": {
                "repository": "goblin-king",
                "digest": self.images["controller"]["digest"].split("@", 1)[1],
            },
            "api": {
                "resources": {
                    "requests": {"cpu": "100m", "memory": "128Mi"},
                    "limits": {"cpu": "1", "memory": "512Mi"},
                }
            },
            "admin": {"enabled": False},
            "workers": {"exampleLongHello": {"enabled": False}},
            "repository": {"enabled": False},
            "networkPolicy": {"enabled": True},
            "persistence": {
                "enabled": True,
                "topology": "sqlite-single-node",
                "sqliteNode": "desktop-worker",
            },
            "config": {
                "registryPath": "/pipeline-config/goblins.json",
                "imagesPath": "/pipeline-config/images.json",
                "redisUrl": "redis://delivery-redis:6379/0",
            },
            "scheduler": {
                "workloadSecurity": {
                    "profile": "restricted-v1",
                    "restricted": {"workerServiceAccounts": {"pipeline.build": "pipeline-build"}},
                }
            },
            "resourcePolicies": {"version": 1, "defaults": policy, "goblins": {}},
            "pipeline": {
                "enabled": True,
                "authSecret": "pipeline-auth",
                "githubSecret": "pipeline-github-app",
                "registryReaderSecret": "registry-reader",
                "settings": {
                    **provider,
                    "acceptance_faults": bool(self.acceptance_id),
                    "worker_policy": policy,
                    "worker_image": self.images["builder"]["digest"],
                    "pipeline_revision": revision,
                    "namespace_prefixes": [self.prefix + "-"],
                    "registry_prefixes": [registry],
                    "registry_endpoint": "https://registry.gk-local-registry.svc",
                    "reconcile_seconds": 5,
                    "release_repository": "fixture/release-state",
                    "release_repository_id": 3,
                },
                "workerConfig": {
                    "revision": revision,
                    "acceptance_faults": bool(self.acceptance_id),
                    "provider": provider,
                    "sources": sources,
                    "helper_image": self.images["controller"]["digest"],
                    "registry_host": registry,
                    "registry_endpoint": "https://registry.gk-local-registry.svc",
                },
            },
            "project": {
                "extraVolumes": [
                    {"name": "pipeline-discovery", "configMap": {"name": "pipeline-discovery"}}
                ],
                "extraVolumeMounts": [
                    {
                        "name": "pipeline-discovery",
                        "mountPath": "/pipeline-config",
                        "readOnly": True,
                    }
                ],
            },
        }
        definitions = json.loads((ROOT / "examples/pipeline/goblins.json").read_text())
        if "admin" in self.images:
            repository, digest = self.images["admin"]["digest"].split("@", 1)
            values["admin"] = {
                "enabled": True,
                "image": {"repository": repository, "digest": digest, "pullPolicy": "IfNotPresent"},
            }
        image_map = {
            "pipeline.build": {
                "image": self.images["builder"]["digest"],
                "context": "workers/pipeline.build",
                "dockerfile": "Dockerfile",
            }
        }
        if "deployer" in self.images:
            values["pipeline"]["settings"]["deploy_image"] = self.images["deployer"]["digest"]
            values["pipeline"]["workerConfig"]["api_url"] = (
                f"http://delivery-api.{self.control}.svc:8000"
            )
            values["pipeline"]["workerConfig"]["releases"] = {
                f"{self.prefix}-{implementation}-{environment}/app": {
                    "environment": environment,
                    "pull_secret": "registry-pull",
                    "persistence": {"enabled": True, "size": "1Gi", "storageClass": "standard"},
                }
                for implementation in ("python", "node")
                for environment in ("test", "prod")
            }
            for environment in ("test", "prod"):
                kind = "pipeline.deploy." + environment
                values["scheduler"]["workloadSecurity"]["restricted"]["workerServiceAccounts"][
                    kind
                ] = "pipeline-deploy-" + environment
                image_map[kind] = {
                    "image": self.images["deployer"]["digest"],
                    "context": "workers/pipeline.deploy",
                    "dockerfile": "Dockerfile",
                }
                definitions["goblins"].append(
                    {
                        "kind": kind,
                        "display_name": "Deploy " + environment,
                        "module": "goblin_king.pipeline_deploy_worker",
                        "entrypoint": "deploy",
                        "timeout_seconds": 900,
                        "max_retries": 0,
                        "metadata": {"validation_input": {"validate": True}},
                    }
                )
        self.apply(
            {
                "apiVersion": "v1",
                "kind": "ConfigMap",
                "metadata": {"name": "pipeline-discovery", "namespace": self.control},
                "data": {
                    "goblins.json": json.dumps(definitions),
                    "images.json": json.dumps({"workers": image_map}),
                },
            }
        )
        (self.output / "values.json").write_text(json.dumps(values, indent=2))
        self.kube("-n", self.provider, "rollout", "status", "deployment/provider", "--timeout=120s")
        print(
            "Local provider and isolated namespaces prepared; "
            f"Helm values: {self.output / 'values.json'}"
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context", default="docker-desktop")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--images", required=True, type=Path)
    parser.add_argument("--prefix", default="gk-delivery")
    parser.add_argument("--acceptance-id")
    args = parser.parse_args()
    DemoPreparation(
        args.context,
        args.output.resolve(),
        json.loads(args.images.read_text()),
        prefix=args.prefix,
        acceptance_id=args.acceptance_id,
    ).prepare()
