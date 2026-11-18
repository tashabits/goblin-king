"""Prepare an isolated, digest-pinned Helm installation for the browser audit."""

import argparse
import json
import secrets
import subprocess
from pathlib import Path
from uuid import uuid4

from pipeline_build_images import source_identity
from pipeline_image_cache import verify_platform_alias

ROOT = Path(__file__).resolve().parents[1]


def prepare(output, namespace, controller, admin, context="docker-desktop"):
    output.mkdir(parents=True, exist_ok=True)
    identity = source_identity()

    def command(args, document=None, timeout=300):
        result = subprocess.run(
            args,
            input=json.dumps(document) if document else None,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
            check=True,
        )
        return result.stdout

    def kube(*args, document=None):
        return command(["kubectl", "--context", context, "-n", namespace, *args], document)

    state_path = output / "installation.json"
    if state_path.exists():
        state = json.loads(state_path.read_text())
        existing = json.loads(kube("get", "namespace", namespace, "-o", "json"))
        if state["namespace_uid"] != existing["metadata"]["uid"]:
            raise RuntimeError("audit namespace identity changed")
    else:
        token = str(uuid4())
        created = json.loads(
            kube(
                "create",
                "-f",
                "-",
                "-o",
                "json",
                document={
                    "apiVersion": "v1",
                    "kind": "Namespace",
                    "metadata": {
                        "name": namespace,
                        "labels": {"goblin-king.io/admin-audit": token},
                    },
                },
            )
        )
        state = {
            "namespace": namespace,
            "namespace_uid": created["metadata"]["uid"],
            "audit_id": token,
            "context": context,
        }
        state_path.write_text(json.dumps(state, indent=2))
    image_map = json.loads((ROOT / "demo-images.json").read_text())
    token_path = output / "admin-token"
    if not token_path.exists():
        token_path.write_text(secrets.token_urlsafe(48))
    kube(
        "apply",
        "-f",
        "-",
        document={
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {"name": "audit-auth"},
            "stringData": {"bootstrap-admin-token": token_path.read_text()},
        },
    )
    tags = {"controller": controller, "admin": admin}
    tags.update({kind: entry["image"] for kind, entry in image_map["workers"].items()})
    images = {}
    for name, tag in tags.items():
        inspected = json.loads(command(["docker", "image", "inspect", tag]))[0]
        images[name] = {"tag": tag, "digest": inspected["RepoDigests"][0], "id": inspected["Id"]}
    for kind, entry in image_map["workers"].items():
        entry["image"] = images[kind]["digest"]
    # Cache exact local tags on each node, then verify their immutable identities.
    cache_images = list(images.items())
    caches = []
    for offset in range(0, len(cache_images), 6):
        group = cache_images[offset : offset + 6]
        name = "audit-image-cache-" + str(offset)
        kube(
            "apply",
            "-f",
            "-",
            document={
                "apiVersion": "apps/v1",
                "kind": "DaemonSet",
                "metadata": {"name": name},
                "spec": {
                    "selector": {"matchLabels": {"app": name}},
                    "template": {
                        "metadata": {"labels": {"app": name}},
                        "spec": {
                            "automountServiceAccountToken": False,
                            "tolerations": [{"operator": "Exists"}],
                            "containers": [
                                {
                                    "name": "image-" + str(i),
                                    "image": entry["tag"],
                                    "imagePullPolicy": "Always",
                                    "command": ["sh", "-c", "sleep 600"],
                                    "resources": {
                                        "requests": {"cpu": "5m", "memory": "16Mi"},
                                        "limits": {"cpu": "100m", "memory": "64Mi"},
                                    },
                                }
                                for i, (_, entry) in enumerate(group)
                            ],
                        },
                    },
                },
            },
        )
        kube("rollout", "status", "daemonset/" + name, "--timeout=240s")
        pods = json.loads(kube("get", "pods", "-l", "app=" + name, "-o", "json"))["items"]
        for pod in pods:
            for status in pod["status"]["containerStatuses"]:
                entry = group[int(status["name"].split("-")[1])][1]
                expected = entry["digest"].split("@")[1]
                if not status["imageID"].endswith(expected):
                    verify_platform_alias(
                        output, pod, expected, entry["tag"], status["imageID"], context
                    )
        caches.extend(pods)
        kube("delete", "daemonset", name, "--wait=true")
    kube(
        "apply",
        "-f",
        "-",
        document={
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {"name": "audit-images"},
            "data": {
                "images.json": json.dumps(image_map),
                "input.json": json.dumps(
                    {
                        "name": "Audit",
                        "message": "admin runtime audit",
                        "target": "Audit",
                        "value": "Audit",
                    }
                ),
            },
        },
    )
    repository, digest = images["controller"]["digest"].split("@")
    admin_repository, admin_digest = images["admin"]["digest"].split("@")
    values = {
        "fullnameOverride": "audit",
        "image": {"repository": repository, "digest": digest},
        "api": {"existingSecret": "audit-auth", "bootstrapAdminToken": ""},
        "persistence": {"topology": "sqlite-single-node", "sqliteNode": "desktop-worker"},
        "config": {"imagesPath": "/audit/images.json", "redisUrl": "redis://audit-redis:6379/0"},
        "scheduler": {"workerImagePullPolicy": "IfNotPresent"},
        "admin": {
            "image": {"repository": admin_repository, "digest": admin_digest},
            "ingress": {"enabled": False},
            "longHelloUrl": "http://audit-long-hello",
        },
        "workers": {"exampleLongHello": {"image": images["example.long-hello"]["digest"]}},
        "project": {
            "extraVolumes": [{"name": "audit-images", "configMap": {"name": "audit-images"}}],
            "extraVolumeMounts": [
                {"name": "audit-images", "mountPath": "/audit", "readOnly": True}
            ],
        },
    }
    path = output / "values.json"
    path.write_text(json.dumps(values, indent=2))
    command(
        [
            "helm",
            "--kube-context",
            context,
            "upgrade",
            "--install",
            "audit",
            str(ROOT / "charts/goblin-king"),
            "-n",
            namespace,
            "-f",
            str(path),
            "--wait",
            "--timeout",
            "5m",
        ],
        timeout=360,
    )
    state.update(
        {
            "images": images,
            "source_sha256": identity,
            "revision": command(["git", "rev-parse", "HEAD"]).strip(),
            "source_dirty": bool(command(["git", "status", "--porcelain"]).strip()),
        }
    )
    state_path.write_text(json.dumps(state, indent=2))
    (output / "image-cache.json").write_text(json.dumps(caches, indent=2))
    kinds = [kind for kind in image_map["workers"] if kind != "example.long-hello"]
    registered = {
        entry["kind"] for entry in json.loads((ROOT / "demo-goblins.json").read_text())["goblins"]
    }
    args = [
        "exec",
        "deploy/audit-scheduler",
        "--",
        "goblin-king",
        "workers",
        "validate",
        "--input",
        "/audit/input.json",
        "--registry",
        "/app/demo-goblins.json",
        "--images",
        "/audit/images.json",
        "--runtime",
        "kubernetes",
        "--db",
        "/data/goblin-king.sqlite3",
        "--redis-url",
        "redis://audit-redis:6379/0",
        "--result-forwarder-image",
        images["controller"]["digest"],
        "--worker-image-pull-policy",
        "IfNotPresent",
        "--result-forwarder-image-pull-policy",
        "IfNotPresent",
        "--timeout-seconds",
        "120",
        "--json",
    ]
    for kind in kinds:
        if kind in registered:
            args += ["--kind", kind]
    validation = command(["kubectl", "--context", context, "-n", namespace, *args], timeout=3600)
    (output / "validation.json").write_text(validation)
    if source_identity() != identity:
        raise RuntimeError(
            "source changed during audit preparation; this is development evidence only"
        )
    print(json.dumps({"namespace": namespace, "status": "ready", "images": len(images)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--controller", required=True)
    parser.add_argument("--admin", required=True)
    args = parser.parse_args()
    prepare(args.output.resolve(), args.namespace, args.controller, args.admin)
