"""Real local Git history with simulated HTTP authentication and conflicting ref updates."""

import json

import pytest
from fastapi.testclient import TestClient

from examples.pipeline.provider_simulator import create_app
from goblin_king.pipeline_github import AppCredentials, GitHubProvider, UnapprovedSource
from goblin_king.pipeline_recording import ReleaseRecorder
from goblin_king.pipeline_state import PipelineConflict


def recording(tmp_path):
    app = create_app(tmp_path / "provider")
    http = TestClient(app)
    provider = GitHubProvider(
        AppCredentials(
            1,
            1,
            tmp_path / "provider/private-key.pem",
            tmp_path / "provider/webhook-secret",
            "http://testserver",
            True,
        )
    )

    def request(path, *, token, body=None, method="GET"):
        response = http.request(
            method, path, json=body, headers={"Authorization": "Bearer " + token}
        )
        if response.status_code >= 400:
            raise UnapprovedSource(str(response.status_code))
        return response.json()

    provider._request = request
    return app, provider, ReleaseRecorder(provider, "fixture/release-state", 3)


def test_recording_is_immutable_and_recovers_lost_acknowledgement(tmp_path):
    app, provider, recorder = recording(tmp_path)
    record = {"image": "registry.test/app@sha256:" + "a" * 64, "health": {"status": 200}}
    first = recorder.record("release-one", "operation-one", record)
    recovered = recorder.record("release-one", "operation-one", record)
    assert first["commit"] == recovered["commit"]
    assert recovered["recovered"]
    with pytest.raises(PipelineConflict, match="immutable"):
        recorder.record("release-one", "operation-one", record | {"active": False})
    raw = provider.content("fixture/release-state", 3, "releases/release-one.json", first["commit"])
    assert json.loads(raw) == record


def test_non_force_retry_preserves_concurrent_service_record(tmp_path, monkeypatch):
    _, provider, recorder = recording(tmp_path)
    other = ReleaseRecorder(provider, "fixture/release-state", 3)
    original = recorder.request
    collided = False

    def collide(path, **kwargs):
        nonlocal collided
        if kwargs.get("method") == "PATCH" and not collided:
            collided = True
            other.record(
                "release-two", "operation-two", {"image": "registry.test/two@sha256:" + "b" * 64}
            )
        return original(path, **kwargs)

    monkeypatch.setattr(recorder, "request", collide)
    result = recorder.record(
        "release-one", "operation-one", {"image": "registry.test/one@sha256:" + "a" * 64}
    )
    for path in [
        "releases/release-one.json",
        "releases/release-two.json",
        "operations/operation-one.json",
        "operations/operation-two.json",
    ]:
        assert provider.content("fixture/release-state", 3, path, result["commit"])
    log = provider.content("fixture/release-state", 3, "CHANGELOG.md", result["commit"]).decode()
    assert log.count("operation-one") == log.count("operation-two") == 1


def test_source_scoped_token_cannot_write_release_history(tmp_path):
    _, provider, _ = recording(tmp_path)
    with pytest.raises(UnapprovedSource, match="403"):
        provider._request(
            "/repos/fixture/release-state/git/refs/heads/main", token=provider.token(1, write=True)
        )
