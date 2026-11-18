"""Run the complete admin audit in isolated headless containers without desktop access."""

from __future__ import annotations

import io
import json
import os
import secrets
import socket
import subprocess
import tarfile
import time
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
BROWSER = (
    "goblin-king-headless-cluster-audit@sha256:"
    "8dcf493a2ed34216f866df3b07d6baefb3c0044faa3468413cd07d7966879387"
)


def command(args, *, document=None, timeout=300):
    result = subprocess.run(
        args,
        input=json.dumps(document) if document else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=timeout,
    )
    if result.returncode:
        raise RuntimeError("admin audit command failed: " + result.stderr[-2000:])
    return result.stdout


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def wait_http(base):
    deadline = time.monotonic() + 120
    while True:
        try:
            with urlopen(base + "/admin/", timeout=3) as response:
                if response.status == 200:
                    return
        except OSError:
            pass
        if time.monotonic() > deadline:
            raise RuntimeError("headless audit admin endpoint did not become available")
        time.sleep(2)


def docker_install(proof, output):
    name = "gk-audit-" + proof.state["run_id"][:12]
    token_path = output / "admin-token"
    if not token_path.exists():
        token_path.write_text(secrets.token_urlsafe(48))
    token = token_path.read_text()
    settings = json.loads((ROOT / "goblin-king-api.json").read_text())
    settings.update(
        registry="/app/demo-goblins.json",
        images="/audit/images.json",
        db="/app/.goblin-king/goblin-king.sqlite3",
        artifact_root="/app/.goblin-king/artifacts",
        resource_policies="/app/goblin-resource-policies.json",
        redis_url="redis://redis:6379/0",
        auth_token=token,
        bootstrap_admin_token=token,
    )
    (output / "api-settings.json").write_text(json.dumps(settings))
    images = json.loads((ROOT / "demo-images.json").read_text())
    for entry in images["workers"].values():
        entry["image"] = json.loads(command(["docker", "image", "inspect", entry["image"]]))[0][
            "RepoDigests"
        ][0]
        if "context" in entry:
            entry["context"] = "/app/" + entry["context"].removeprefix("./")
    (output / "images.json").write_text(json.dumps(images))
    (output / "input.json").write_text(
        json.dumps(
            {"message": "admin runtime audit", "name": "Audit", "target": "Audit", "value": "Audit"}
        )
    )
    image = proof.state["images"]["controller"]["digest"]
    environment = {
        "GOBLIN_KING_DOCKER_DATA_VOLUME": name + "_data",
        "GOBLIN_KING_RUN_ROOT": "/app/.goblin-king/runs",
        "GOBLIN_KING_DOCKER_NETWORK": name + "_default",
        "GOBLIN_KING_REDIS_URL": "redis://redis:6379/0",
        "GOBLIN_KING_REPOSITORY_ENABLED": "false",
    }
    mounts = [
        "data:/app/.goblin-king",
        "/var/run/docker.sock:/var/run/docker.sock",
        str(output) + ":/audit:ro",
    ]
    redis = json.loads(command(["docker", "image", "inspect", "redis:7-alpine"]))[0]["RepoDigests"][
        0
    ]
    compose = {
        "name": name,
        "services": {
            "redis": {"image": redis},
            "api": {
                "image": image,
                "environment": environment
                | {
                    "GOBLIN_KING_BOOTSTRAP_ADMIN_TOKEN": token,
                    "GOBLIN_KING_API_TOKEN": token,
                    "GOBLIN_KING_NOTEBOOK_SERVICE_RUNTIME": "docker",
                },
                "volumes": mounts,
                "command": [
                    "api",
                    "run",
                    "--settings",
                    "/audit/api-settings.json",
                    "--host",
                    "0.0.0.0",
                    "--port",
                    "8000",
                ],
            },
            "scheduler": {
                "image": image,
                "environment": environment,
                "volumes": mounts,
                "command": [
                    "scheduler",
                    "run",
                    "--registry",
                    "/app/demo-goblins.json",
                    "--images",
                    "/audit/images.json",
                    "--db",
                    "/app/.goblin-king/goblin-king.sqlite3",
                    "--redis-url",
                    "redis://redis:6379/0",
                ],
            },
            "admin": {
                "image": proof.state["images"]["admin"]["digest"],
                "environment": {
                    "API_UPSTREAM": "api:8000",
                    "LONG_HELLO_URL": "http://long-hello:8080",
                },
                "ports": ["127.0.0.1::8080"],
            },
            "long-hello": {"image": images["workers"]["example.long-hello"]["image"]},
        },
        "volumes": {"data": {"name": name + "_data"}},
    }
    path = output / "compose.json"
    path.write_text(json.dumps(compose, indent=2))
    command(["docker", "compose", "-f", str(path), "up", "-d", "--wait", "--wait-timeout", "180"])
    api = command(["docker", "compose", "-f", str(path), "ps", "-q", "api"]).strip()
    args = [
        "docker",
        "exec",
        api,
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
        "docker",
        "--db",
        "/app/.goblin-king/goblin-king.sqlite3",
        "--redis-url",
        "redis://redis:6379/0",
        "--timeout-seconds",
        "120",
        "--json",
    ]
    for row in json.loads((ROOT / "demo-goblins.json").read_text())["goblins"]:
        if row["kind"] != "example.long-hello":
            args += ["--kind", row["kind"]]
    (output / "validation.json").write_text(command(args, timeout=3600))
    address = command(["docker", "compose", "-f", str(path), "port", "admin", "8080"]).strip()
    return {"name": name, "token": token, "base": "http://" + address, "compose": path}


def audit(proof, runtime):
    from pipeline_admin_acceptance import verify

    output = proof.output / (runtime + "-admin-audit")
    output.mkdir(parents=True, exist_ok=True)
    installation = output / "installation"
    installation.mkdir(exist_ok=True)
    forwarding = None
    previous_token = os.environ.get("GOBLIN_KING_AUDIT_TOKEN")
    config = {
        "runtime": runtime,
        "revision": proof.state["revision"],
        "source_sha256": proof.state["images"]["controller"]["source_sha256"],
        "acceptance_run_id": proof.state["run_id"],
    }
    if runtime == "docker":
        installed = docker_install(proof, installation)
        config.update(
            api_base_url=installed["base"],
            browser_base_url="http://admin:8080",
            long_service_url="http://long-hello:8080",
        )
        token = installed["token"]
        config_path = installation / "browser-config.json"
        config_path.write_text(json.dumps(config))
        env_path = installation / "browser.env"
        env_path.write_text("GOBLIN_KING_AUDIT_TOKEN=" + token + "\n")
    else:
        from pipeline_admin_prepare import prepare

        namespace = "gk-admin-" + proof.state["run_id"][:12]
        prepare(
            installation,
            namespace,
            proof.state["images"]["controller"]["tag"],
            proof.state["images"]["admin"]["tag"],
            proof.state["context"],
        )
        kube = ["kubectl", "--context", proof.state["context"], "-n", namespace]
        record = json.loads(command(kube + ["get", "namespace", namespace, "-o", "json"]))
        command(
            kube
            + [
                "label",
                "namespace",
                namespace,
                "goblin-king.io/acceptance=" + proof.state["run_id"],
            ]
        )
        proof.state["namespaces"][namespace] = {"name": namespace, "uid": record["metadata"]["uid"]}
        proof.save()
        token = (installation / "admin-token").read_text()
        port = free_port()
        forward_log = (installation / "port-forward.log").open("w")
        forwarding = subprocess.Popen(
            kube
            + ["port-forward", "service/audit-admin", str(port) + ":8080", "--address=127.0.0.1"],
            stdout=forward_log,
            stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        config.update(
            api_base_url="http://127.0.0.1:" + str(port),
            browser_base_url="http://audit-admin:8080",
            long_service_url="http://audit-long-hello",
        )
    try:
        wait_http(config["api_base_url"])
        os.environ["GOBLIN_KING_AUDIT_TOKEN"] = token
        if runtime == "docker":
            command(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--network",
                    installed["name"] + "_default",
                    "--cap-drop=ALL",
                    "--security-opt=no-new-privileges",
                    "--memory=1g",
                    "--cpus=1",
                    "--env-file",
                    str(env_path),
                    "-e",
                    "AUDIT_CONFIG=/inputs/config.json",
                    "-e",
                    "AUDIT_OUTPUT=/proof",
                    "--mount",
                    "type=bind,source="
                    + str(ROOT / "scripts/pipeline_admin_browser.cjs")
                    + ",target=/inputs/run.cjs,readonly",
                    "--mount",
                    "type=bind,source=" + str(config_path) + ",target=/inputs/config.json,readonly",
                    "--mount",
                    "type=bind,source=" + str(output) + ",target=/proof",
                    "--entrypoint",
                    "node",
                    BROWSER,
                    "/inputs/run.cjs",
                ],
                timeout=1800,
            )
        else:
            helm_browser(kube, config, output)
        result = verify(proof, runtime)
        if runtime == "docker":
            command(
                [
                    "docker",
                    "compose",
                    "-f",
                    str(installed["compose"]),
                    "down",
                    "--volumes",
                    "--timeout",
                    "30",
                ]
            )
            (output / "cleanup.json").write_text(
                json.dumps({"compose_project": installed["name"], "removed": True})
            )
        return result
    finally:
        if forwarding:
            forwarding.terminate()
            forwarding.wait(timeout=15)
            forward_log.close()
        if previous_token is None:
            os.environ.pop("GOBLIN_KING_AUDIT_TOKEN", None)
        else:
            os.environ["GOBLIN_KING_AUDIT_TOKEN"] = previous_token


def helm_browser(kube, config, output):
    name = "headless-admin-" + secrets.token_hex(4)
    command(
        kube + ["create", "-f", "-"],
        document={
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {"name": name},
            "data": {
                "config.json": json.dumps(config),
                "run.cjs": (ROOT / "scripts/pipeline_admin_browser.cjs").read_text(),
            },
        },
    )
    pod = {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {"name": name},
        "spec": {
            "restartPolicy": "Never",
            "automountServiceAccountToken": False,
            "activeDeadlineSeconds": 2400,
            "nodeSelector": {"kubernetes.io/hostname": "desktop-worker"},
            "securityContext": {
                "runAsNonRoot": True,
                "runAsUser": 1000,
                "runAsGroup": 1000,
                "fsGroup": 1000,
            },
            "containers": [
                {
                    "name": "browser",
                    "image": BROWSER,
                    "command": [
                        "node",
                        "-e",
                        "require('/inputs/run.cjs');setInterval(()=>{},1000)",
                    ],
                    "env": [
                        {
                            "name": "GOBLIN_KING_AUDIT_TOKEN",
                            "valueFrom": {
                                "secretKeyRef": {
                                    "name": "audit-auth",
                                    "key": "bootstrap-admin-token",
                                }
                            },
                        },
                        {"name": "AUDIT_CONFIG", "value": "/inputs/config.json"},
                        {"name": "AUDIT_OUTPUT", "value": "/proof"},
                    ],
                    "securityContext": {
                        "allowPrivilegeEscalation": False,
                        "capabilities": {"drop": ["ALL"]},
                    },
                    "resources": {
                        "requests": {"cpu": "100m", "memory": "256Mi"},
                        "limits": {"cpu": "1", "memory": "1Gi"},
                    },
                    "volumeMounts": [
                        {"name": "inputs", "mountPath": "/inputs", "readOnly": True},
                        {"name": "proof", "mountPath": "/proof"},
                        {"name": "shm", "mountPath": "/dev/shm"},
                    ],
                }
            ],
            "volumes": [
                {"name": "inputs", "configMap": {"name": name}},
                {"name": "proof", "emptyDir": {"sizeLimit": "256Mi"}},
                {"name": "shm", "emptyDir": {"medium": "Memory", "sizeLimit": "128Mi"}},
            ],
        },
    }
    observed = json.loads(command(kube + ["create", "-f", "-", "-o", "json"], document=pod))
    command(kube + ["wait", "--for=condition=Ready", "pod/" + name, "--timeout=120s"])
    deadline = time.monotonic() + 1800
    while True:
        result = subprocess.run(
            kube
            + [
                "exec",
                name,
                "--",
                "node",
                "-e",
                "const f=require('fs');process.exit(f.existsSync('/proof/browser-error.txt')||"
                "(f.existsSync('/proof/browser-proof.json')&&JSON.parse(f.readFileSync('/proof/browser-proof.json')).completed)?0:1)",
            ],
            capture_output=True,
            timeout=30,
        )
        if result.returncode == 0:
            break
        if time.monotonic() >= deadline:
            raise RuntimeError("headless Helm audit timed out; evidence retained in " + name)
        time.sleep(3)
    raw = subprocess.check_output(
        kube + ["exec", name, "--", "tar", "-cf", "-", "-C", "/proof", "."], timeout=60
    )
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        archive.extractall(output, filter="data")
    if (output / "browser-error.txt").exists():
        raise RuntimeError((output / "browser-error.txt").read_text())
    namespace = observed["metadata"]["namespace"]
    command(
        kube + ["delete", "--raw", f"/api/v1/namespaces/{namespace}/pods/{name}", "-f", "-"],
        document={
            "apiVersion": "v1",
            "kind": "DeleteOptions",
            "preconditions": {"uid": observed["metadata"]["uid"]},
        },
    )
    command(kube + ["delete", "configmap", name])
