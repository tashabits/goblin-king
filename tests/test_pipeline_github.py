"""Authenticate local provider events and require authoritative reviewed commits."""

import hashlib
import hmac
import json

import pytest

from goblin_king.pipeline_github import UnapprovedSource
from tests.test_pipeline_recording import recording
from tests.test_pipeline_store import registration


def fixture(tmp_path):
    app, provider, _ = recording(tmp_path)
    reg = registration().model_copy(update={"source_repository": "fixture/python-api"})
    reg = reg.model_copy(
        update={
            "environments": tuple(
                route.model_copy(update={"branch": "main" if route.name == "prod" else "test"})
                for route in reg.environments
            )
        }
    )
    return app.state.simulator, provider, reg


def test_raw_signature_installation_and_current_review_evidence(tmp_path):
    simulator, provider, reg = fixture(tmp_path)
    delivery = simulator.merge(
        {"repository": "fixture/python-api", "branch": "test", "webhook": False}
    )
    raw = json.dumps(delivery["event"]).encode()
    signature = (
        "sha256="
        + hmac.new(simulator.secret("webhook-secret").encode(), raw, hashlib.sha256).hexdigest()
    )
    assert provider.verify_webhook(raw, signature) == delivery["event"]
    with pytest.raises(UnapprovedSource, match="signature"):
        provider.verify_webhook(raw + b" ", signature)
    wrong = json.dumps(delivery["event"] | {"installation": {"id": 99}}).encode()
    signature = (
        "sha256="
        + hmac.new(simulator.secret("webhook-secret").encode(), wrong, hashlib.sha256).hexdigest()
    )
    with pytest.raises(UnapprovedSource, match="installation"):
        provider.verify_webhook(wrong, signature)
    event = delivery["event"]
    approved = provider.verify_merge(reg, 1, "test", event["after"], event["before"])
    assert approved["simulated"] and approved["reviews"]
    repository = simulator.state["repositories"]["fixture/python-api"]
    review = repository["pulls"][-1]["reviews"][0]
    repository["pulls"][-1]["reviews"].append(review | {"id": 999, "state": "CHANGES_REQUESTED"})
    with pytest.raises(UnapprovedSource, match="reviewed merge"):
        provider.verify_merge(reg, 1, "test", event["after"], event["before"])


def test_delayed_event_and_unreviewed_head_are_not_admitted(tmp_path):
    simulator, provider, reg = fixture(tmp_path)
    first = simulator.merge(
        {"repository": "fixture/python-api", "branch": "test", "webhook": False}
    )["event"]
    second = simulator.merge(
        {"repository": "fixture/python-api", "branch": "test", "webhook": False, "unreviewed": True}
    )["event"]
    with pytest.raises(UnapprovedSource, match="authoritative"):
        provider.verify_merge(reg, 1, "test", first["after"], first["before"])
    with pytest.raises(UnapprovedSource, match="reviewed merge"):
        provider.verify_merge(reg, 1, "test", second["after"], first["before"])
    assert not provider.verify_merge(reg, 1, "test", second["after"], second["after"])


def test_discovery_uses_root_dockerfile_and_reports_missing_test_target(tmp_path):
    simulator, provider, reg = fixture(tmp_path)
    result = provider.discover(reg, 1)
    assert all(
        route["contract"]["context"] == "." and route["contract"]["dockerfile"] == "Dockerfile"
        for route in result["routes"]
    )
    _, directory = simulator.repository("fixture", "python-api")
    simulator.git(directory, "checkout", "test")
    (directory / "Dockerfile").write_text("FROM scratch\n")
    simulator.git(directory, "add", "Dockerfile")
    simulator.git(directory, "commit", "-m", "Missing test target fixture")
    with pytest.raises(UnapprovedSource, match="missing target"):
        provider.discover(reg, 1)
