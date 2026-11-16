"""Delivery API authentication and enabled-service lifecycle regressions."""

from pathlib import Path

from fastapi.testclient import TestClient

from goblin_king.api import create_app
from goblin_king.api_settings import ApiSettings
from goblin_king.pipeline_settings import PipelineSettings


def test_enabled_service_starts_and_stops_without_admitting_old_work(tmp_path):
    settings = ApiSettings(
        registry=Path("examples/goblins.json").resolve(),
        images=Path("goblin-images.json").resolve(),
        db=tmp_path / "db.sqlite3",
        artifact_root=tmp_path / "artifacts",
        auth_token="fixture-token",
        pipeline=PipelineSettings(
            enabled=True, simulated_provider=True, provider_url="http://127.0.0.1:9"
        ),
    )
    app = create_app(settings)
    with TestClient(app) as client:
        assert client.get("/pipeline/status").status_code == 401
        headers = {"Authorization": "Bearer fixture-token"}
        response = client.get("/pipeline/status", headers=headers)
        assert response.status_code == 200
        assert response.json()["simulated_provider"] is True
        inventory = client.get("/pipeline/recovery", headers=headers)
        assert inventory.status_code == 200
        assert inventory.json()["releases"] == []
        assert app.state.pipeline.thread.is_alive()
        assert client.get("/pipeline/builds?project_id=fixture", headers=headers).json() == {
            "items": []
        }
        denied = client.post("/jobs", headers=headers, json={"kind": "pipeline.build", "input": {}})
        assert denied.status_code == 403
    assert not app.state.pipeline.thread.is_alive()
