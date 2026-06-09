"""Tests for the final optional Kubernetes, admin, and sample goblin phase."""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from fastapi.testclient import TestClient

from goblin_king.api import create_app
from goblin_king.api_settings import ApiSettings
from goblin_king.contracts import GoblinContext
from goblin_king.registry import GoblinRegistry
from goblin_king.runtime import InProcessRuntime
from goblin_king.store import SQLiteStore
from goblin_king.workers import WorkerImageMap


def _client(tmp_path: Path) -> tuple[TestClient, SQLiteStore]:
    settings = ApiSettings(
        registry=Path("examples/goblins.json").resolve(),
        images=Path("goblin-images.json").resolve(),
        db=tmp_path / "api.sqlite3",
        redis_url="redis://localhost:6379/0",
        artifact_root=tmp_path / "artifacts",
        auth_token="test-token",
    )
    return TestClient(create_app(settings)), SQLiteStore(settings.db)


def _auth() -> dict[str, str]:
    return {"Authorization": "Bearer test-token"}


def test_short_hello_goblin_returns_hello_world() -> None:
    """Verify the one-shot Hello World sample works through the public contract."""
    registry = GoblinRegistry.from_path("examples/goblins.json")
    definition, entrypoint = registry.resolve("example.hello")
    context = GoblinContext(run_id="run-hello", artifact_root=".goblin-king/artifacts/run-hello")

    result = InProcessRuntime().run(definition, entrypoint, {}, context)

    assert result.status == "success"
    assert result.data["canonical_message"] == "Hello World"


def test_sample_worker_image_map_covers_demo_goblins() -> None:
    """Verify every final sample has a self-contained worker image entry."""
    worker_map = WorkerImageMap.from_path("goblin-images.json")

    for kind in {
        "example.hello",
        "example.long-hello",
        "example.artifact",
        "example.environment",
        "example.controlled-failure",
        "example.progress",
    }:
        worker = worker_map.get(kind)
        assert worker_map.resolved_context(worker).joinpath(worker.dockerfile).exists()


def test_admin_ui_requires_auth_and_renders_goblin_inventory(tmp_path: Path) -> None:
    """Verify the API admin route points users toward the React admin service."""
    client, _ = _client(tmp_path)

    rendered = client.get("/admin")

    assert rendered.status_code == 200
    assert "Goblin King React Admin" in rendered.text
    assert "separate React admin service" in rendered.text


def test_long_running_service_registration_and_probe_records_traffic(tmp_path: Path) -> None:
    """Verify service probes persist request/response evidence and changing timestamps."""
    timestamps: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            timestamp = datetime.now(UTC).isoformat()
            timestamps.append(timestamp)
            body = json.dumps(
                {
                    "message": "Hello World from long running service",
                    "timestamp": timestamp,
                }
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *args: object) -> None:
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client, store = _client(tmp_path)
    base_url = f"http://127.0.0.1:{server.server_port}"

    try:
        created = client.post(
            "/services/long-running",
            json={"kind": "example.long-hello", "base_url": base_url},
            headers=_auth(),
        )
        service_id = created.json()["id"]
        first = client.post(f"/services/long-running/{service_id}/probe", headers=_auth())
        second = client.post(f"/services/long-running/{service_id}/probe", headers=_auth())
    finally:
        server.shutdown()

    assert created.status_code == 200
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["response"]["json"]["message"] == "Hello World from long running service"
    first_timestamp = first.json()["response"]["json"]["timestamp"]
    second_timestamp = second.json()["response"]["json"]["timestamp"]
    assert first_timestamp != second_timestamp
    assert store.get_long_service(service_id).status == "running"  # type: ignore[union-attr]
    assert store.list_events(event_type="admin.service.probed")


def test_long_running_service_detail_stop_and_stopped_probe_conflict(tmp_path: Path) -> None:
    """Verify service stop supports tester kill controls without hard-killing containers."""
    client, store = _client(tmp_path)
    created = client.post(
        "/services/long-running",
        json={"kind": "example.long-hello", "base_url": "http://service.example"},
        headers=_auth(),
    )
    service_id = created.json()["id"]

    detail = client.get(f"/services/long-running/{service_id}", headers=_auth())
    stopped = client.post(f"/services/long-running/{service_id}/stop", headers=_auth())
    probe = client.post(f"/services/long-running/{service_id}/probe", headers=_auth())

    assert created.status_code == 200
    assert detail.status_code == 200
    assert detail.json()["id"] == service_id
    assert stopped.status_code == 200
    assert stopped.json()["status"] == "stopped"
    assert probe.status_code == 409
    assert store.get_long_service(service_id).status == "stopped"  # type: ignore[union-attr]
    assert store.list_events(event_type="admin.service.stopped")


def test_helm_chart_includes_optional_default_on_ingress() -> None:
    """Verify the optional Kubernetes chart includes admin ingress controls."""
    values = Path("charts/goblin-king/values.yaml").read_text(encoding="utf-8")
    ingress = Path("charts/goblin-king/templates/ingress.yaml").read_text(encoding="utf-8")
    api = Path("charts/goblin-king/templates/api.yaml").read_text(encoding="utf-8")
    config = Path("charts/goblin-king/templates/configmap.yaml").read_text(encoding="utf-8")
    scheduler = Path("charts/goblin-king/templates/scheduler.yaml").read_text(encoding="utf-8")
    long_hello = Path("charts/goblin-king/templates/long-hello.yaml").read_text(
        encoding="utf-8"
    )
    host_values = Path("examples/adopting-project/helm-values.yaml").read_text(
        encoding="utf-8"
    )
    compose_extension = Path(
        "examples/adopting-project/docker-compose.host-project.yml"
    ).read_text(encoding="utf-8")

    assert "enabled: true" in values
    assert "kind: Ingress" in ingress
    assert ".Values.admin.ingress.enabled" in ingress
    assert "goblin-king-api.json" in api
    assert "projectSettingsPath" in values
    assert '"project": "{{ .Values.config.projectSettingsPath }}"' in config
    assert "--project" in scheduler
    assert "extraVolumeMounts" in api
    assert "extraLongServices" in long_hello
    assert "project-goblin-config" in host_values
    assert "--project" in compose_extension
    assert "worker-project-maintenance-hello" in compose_extension
