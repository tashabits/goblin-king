"""Tests for the final optional Kubernetes, admin, and sample goblin phase."""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from goblin_king.contracts import GoblinContext
from goblin_king.registry import GoblinRegistry
from goblin_king.runtime import InProcessRuntime
from goblin_king.workers import WorkerImageMap
from tests.api_helpers import auth_headers, build_api_client


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
    client, _, _ = build_api_client(tmp_path)

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
    client, store, _ = build_api_client(tmp_path)
    base_url = f"http://127.0.0.1:{server.server_port}"

    try:
        created = client.post(
            "/services/long-running",
            json={"kind": "example.long-hello", "base_url": base_url},
            headers=auth_headers(),
        )
        service_id = created.json()["id"]
        first = client.post(f"/services/long-running/{service_id}/probe", headers=auth_headers())
        second = client.post(f"/services/long-running/{service_id}/probe", headers=auth_headers())
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
    client, store, _ = build_api_client(tmp_path)
    created = client.post(
        "/services/long-running",
        json={"kind": "example.long-hello", "base_url": "http://service.example"},
        headers=auth_headers(),
    )
    service_id = created.json()["id"]

    detail = client.get(f"/services/long-running/{service_id}", headers=auth_headers())
    stopped = client.post(f"/services/long-running/{service_id}/stop", headers=auth_headers())
    probe = client.post(f"/services/long-running/{service_id}/probe", headers=auth_headers())

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
    assert '"oidc"' in config
    assert "jwks_url" in config
    assert "--project" in scheduler
    assert "extraVolumeMounts" in api
    assert "extraLongServices" in long_hello
    assert "project-goblin-config" in host_values
    assert "--project" in compose_extension
    assert "worker-project-maintenance-hello" in compose_extension


def test_helm_chart_includes_cloud_neutral_production_controls() -> None:
    """Verify the chart exposes generic production hardening knobs."""
    values = Path("charts/goblin-king/values.yaml").read_text(encoding="utf-8")
    helpers = Path("charts/goblin-king/templates/_helpers.tpl").read_text(encoding="utf-8")
    api = Path("charts/goblin-king/templates/api.yaml").read_text(encoding="utf-8")
    scheduler = Path("charts/goblin-king/templates/scheduler.yaml").read_text(
        encoding="utf-8"
    )
    serviceaccounts = Path("charts/goblin-king/templates/serviceaccounts.yaml").read_text(
        encoding="utf-8"
    )
    pvc = Path("charts/goblin-king/templates/pvc.yaml").read_text(encoding="utf-8")
    hpa = Path("charts/goblin-king/templates/hpa.yaml").read_text(encoding="utf-8")
    pdb = Path("charts/goblin-king/templates/pdb.yaml").read_text(encoding="utf-8")
    network_policy = Path("charts/goblin-king/templates/networkpolicy.yaml").read_text(
        encoding="utf-8"
    )
    ingress = Path("charts/goblin-king/templates/ingress.yaml").read_text(
        encoding="utf-8"
    )

    assert "pullSecrets: []" in values
    assert "existingSecretBootstrapTokenKey" in values
    assert "podSecurityContext" in values
    assert "securityContext" in values
    assert "autoscaling:" in values
    assert "podDisruptionBudget:" in values
    assert "networkPolicy:" in values
    assert "accessModes:" in values
    assert "goblin-king.podPlacement" in helpers
    assert "goblin-king.serviceAccountName" in helpers
    assert "secretKeyRef" in api
    assert "serviceAccountName" in api
    assert "resources:" in api
    assert "nodeSelector:" in helpers
    assert "RoleBinding" in scheduler
    assert "kind: ServiceAccount" in serviceaccounts
    assert "toYaml .Values.persistence.accessModes" in pvc
    assert "kind: HorizontalPodAutoscaler" in hpa
    assert "autoscaling/v2" in hpa
    assert "kind: PodDisruptionBudget" in pdb
    assert "kind: NetworkPolicy" in network_policy
    assert "tls:" in ingress
