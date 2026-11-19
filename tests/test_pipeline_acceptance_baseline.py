"""Shared Gateway infrastructure remains protected unless its owner is an acceptance fixture."""

import importlib
import json
from pathlib import Path

import pytest


def resource(kind, name, *, namespace=None, labels=None):
    return {
        "kind": kind,
        "metadata": {"uid": name, "name": name, "namespace": namespace, "labels": labels or {}},
        "spec": {"test": name},
    }


@pytest.mark.parametrize("exclude_acceptance", [False, True])
def test_baseline_preserves_unrelated_shared_gateway_resources(
    monkeypatch, tmp_path, exclude_acceptance
):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    module = importlib.import_module("pipeline_acceptance")
    labels = {
        "app.kubernetes.io/component": "proxy",
        "app.kubernetes.io/managed-by": "envoy-gateway",
        "app.kubernetes.io/name": "envoy",
        "gateway.envoyproxy.io/owning-gateway-name": "test",
        "gateway.envoyproxy.io/owning-gateway-namespace": "acceptance",
    }
    items = [
        resource("Namespace", "acceptance", labels={"goblin-king.io/acceptance": "run"}),
        resource("Namespace", "shared"),
        resource("Namespace", "unrelated"),
        resource("Deployment", "local-app", namespace="acceptance"),
        resource("Deployment", "controller", namespace="shared"),
        resource("Deployment", "proxy-deployment", namespace="shared", labels=labels),
        resource("Service", "proxy-service", namespace="shared", labels=labels),
        resource("StatefulSet", "different-kind", namespace="shared", labels=labels),
    ]
    for key in labels:
        incomplete = {name: value for name, value in labels.items() if name != key}
        items.append(
            resource("Deployment", "missing-" + key, namespace="shared", labels=incomplete)
        )
    for owner in ("unrelated", "missing-namespace", ""):
        items.append(
            resource(
                "Service", "owner-" + owner, namespace="shared",
                labels={**labels, "gateway.envoyproxy.io/owning-gateway-namespace": owner},
            )
        )
    proof = module.Acceptance(tmp_path)
    monkeypatch.setattr(proof, "kube", lambda *args: json.dumps({"items": items}))
    baseline = proof.baseline(exclude_acceptance=exclude_acceptance)
    excluded = {"acceptance", "local-app", "proxy-deployment", "proxy-service"}
    expected = {item["metadata"]["uid"] for item in items}
    assert set(baseline) == expected - (excluded if exclude_acceptance else set())
    assert baseline["controller"]["namespace"] == "shared"
    assert len(baseline["controller"]["spec_sha256"]) == 64
