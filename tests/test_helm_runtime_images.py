"""Rendered Helm contract for control-plane and generated workload images."""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest
import yaml

CONTROL_DIGEST = "sha256:" + "a" * 64
FORWARDER_DIGEST = "sha256:" + "b" * 64


pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="Helm is not installed")


def test_legacy_tag_image_and_pull_secret_shapes_remain_compatible() -> None:
    string_documents = _helm_documents(
        "--set-string",
        "image.repository=registry.example/control",
        "--set-string",
        "image.tag=v1",
        "--set-string",
        "image.pullSecrets[0]=registry-string",
    )
    string_scheduler = _deployment(string_documents, "scheduler")
    string_pod = string_scheduler["spec"]["template"]["spec"]
    string_args = string_pod["containers"][0]["command"]

    assert string_pod["containers"][0]["image"] == "registry.example/control:v1"
    assert _option(string_args, "--result-forwarder-image") == "registry.example/control:v1"
    assert string_pod["imagePullSecrets"] == ["registry-string"]
    assert _option_values(string_args, "--workload-image-pull-secret") == [
        "registry-string"
    ]

    map_documents = _helm_documents("--set", "image.pullSecrets[0].name=registry-map")
    map_scheduler = _deployment(map_documents, "scheduler")
    map_pod = map_scheduler["spec"]["template"]["spec"]
    assert map_pod["imagePullSecrets"] == [{"name": "registry-map"}]


def test_digest_precedence_and_separate_forwarder_settings_render_exactly() -> None:
    default_documents = _helm_documents(
        "--set-string",
        "image.repository=registry.example/control",
        "--set-string",
        "image.tag=ignored",
        "--set-string",
        f"image.digest={CONTROL_DIGEST}",
    )
    default_scheduler = _deployment(default_documents, "scheduler")
    default_container = default_scheduler["spec"]["template"]["spec"]["containers"][0]
    control_image = f"registry.example/control@{CONTROL_DIGEST}"
    assert default_container["image"] == control_image
    assert _option(default_container["command"], "--result-forwarder-image") == control_image

    documents = _helm_documents(
        "--set-string",
        "image.repository=registry.example/control",
        "--set-string",
        f"image.digest={CONTROL_DIGEST}",
        "--set",
        "image.pullSecrets[0].name=registry-main",
        "--set-string",
        "scheduler.resultForwarder.image.repository=registry.example/forwarder",
        "--set-string",
        f"scheduler.resultForwarder.image.digest={FORWARDER_DIGEST}",
        "--set-string",
        "scheduler.workerImagePullPolicy=Never",
        "--set-string",
        "scheduler.resultForwarder.pullPolicy=Always",
        "--set-string",
        "scheduler.workloadImagePullSecrets[0]=registry-backup",
    )
    scheduler = _deployment(documents, "scheduler")
    args = scheduler["spec"]["template"]["spec"]["containers"][0]["command"]
    forwarder_image = f"registry.example/forwarder@{FORWARDER_DIGEST}"

    assert _option(args, "--result-forwarder-image") == forwarder_image
    assert _option(args, "--worker-image-pull-policy") == "Never"
    assert _option(args, "--result-forwarder-image-pull-policy") == "Always"
    assert _option_values(args, "--workload-image-pull-secret") == [
        "registry-main",
        "registry-backup",
    ]
    assert _api_config(documents)["kubernetes_runtime"] == {
        "result_forwarder_image": forwarder_image,
        "worker_image_pull_policy": "Never",
        "result_forwarder_image_pull_policy": "Always",
        "workload_image_pull_secret_names": ["registry-main", "registry-backup"],
    }


def _helm_documents(*arguments: str) -> list[dict]:
    completed = subprocess.run(
        ["helm", "template", "issue146", "charts/goblin-king", *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return [document for document in yaml.safe_load_all(completed.stdout) if document]


def _deployment(documents: list[dict], component: str) -> dict:
    return next(
        document
        for document in documents
        if document.get("kind") == "Deployment"
        and document["metadata"]["name"].endswith(f"-{component}")
    )


def _api_config(documents: list[dict]) -> dict:
    config = next(
        document
        for document in documents
        if document.get("kind") == "ConfigMap"
        and "goblin-king-api.json" in document.get("data", {})
    )
    return json.loads(config["data"]["goblin-king-api.json"])


def _option(arguments: list[str], name: str) -> str:
    return arguments[arguments.index(name) + 1]


def _option_values(arguments: list[str], name: str) -> list[str]:
    return [arguments[index + 1] for index, value in enumerate(arguments) if value == name]
