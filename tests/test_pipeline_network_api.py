"""Networking endpoints retain API authentication and installation boundaries."""

from pathlib import Path

from fastapi.testclient import TestClient

from goblin_king.api import create_app
from goblin_king.api_settings import ApiSettings
from goblin_king.pipeline_settings import PipelineSettings
from tests.test_pipeline_network_management import setup
from tests.test_pipeline_networking import configuration


def test_network_api_auth_preview_and_missing_execution(tmp_path):
    manager, key = setup(tmp_path)
    settings = ApiSettings(
        registry=Path("examples/goblins.json").resolve(),
        images=Path("goblin-images.json").resolve(),
        db=tmp_path / "state.sqlite3",
        artifact_root=tmp_path / "artifacts",
        auth_token="fixture-token",
        pipeline=PipelineSettings(
            enabled=True,
            simulated_provider=True,
            provider_url="http://127.0.0.1:9",
            networking=manager.policy,
        ),
    )
    app = create_app(settings)
    with TestClient(app) as client:
        endpoint = f"/pipeline/releases/{key}/networking"
        assert client.get(endpoint).status_code == 401
        headers = {"Authorization": "Bearer fixture-token"}
        inspected = client.get(endpoint, headers=headers)
        assert inspected.status_code == 200
        assert inspected.json()["observed"] is None
        capabilities = client.get(
            "/pipeline/networking/capabilities?project_id=sample-project", headers=headers
        ).json()
        assert capabilities["execution_available"] is False
        assert "test-tls" not in str(capabilities)
        preview = client.post(
            "/pipeline/networking/preview",
            headers=headers,
            json={"release_key": key, "configuration": configuration()},
        )
        assert preview.status_code == 200
        approved = preview.json()
        response = client.post(
            endpoint,
            headers=headers,
            json={
                "configuration": approved["configuration"],
                "preview_id": approved["preview_id"],
                "expected_version": approved["expected_version"],
                "idempotency_key": "one",
            },
        )
        assert response.status_code == 503
        assert manager.inspect(key)["network_version"] == 0
        invalid = configuration()
        invalid["ingress"]["exposure"] = "external"
        assert (
            client.post(
                "/pipeline/networking/preview",
                headers=headers,
                json={"release_key": key, "configuration": invalid},
            ).status_code
            == 422
        )
