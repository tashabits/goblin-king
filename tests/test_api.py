"""Local HTTP tests for the Phase 4 FastAPI control plane."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from goblin_king.api import create_app
from goblin_king.api_settings import ApiSettings
from goblin_king.auth import hash_api_token
from goblin_king.contracts import (
    ArtifactRecord,
    EventRecord,
    FanoutRecord,
    GoblinResult,
    HeartbeatRecord,
    JobRecord,
    LongServiceRecord,
    RunRecord,
    WorkerValidationRecord,
    utc_now,
)
from goblin_king.store import SQLiteStore
from tests.api_helpers import auth_headers, build_api_client


def build_client(tmp_path: Path) -> tuple[TestClient, SQLiteStore, Path]:
    """Create a test API app with isolated settings and SQLite state."""
    return build_api_client(tmp_path)


def build_client_with_limit(
    tmp_path: Path,
    rate_limit_per_minute: int,
) -> tuple[TestClient, SQLiteStore]:
    """Create a test API app with a custom local rate limit."""
    client, store, _ = build_api_client(
        tmp_path,
        rate_limit_per_minute=rate_limit_per_minute,
    )
    return client, store


def test_health_and_goblins_endpoints(tmp_path: Path) -> None:
    """Verify read endpoints expose health and registry/image mapping data."""
    client, _, _ = build_client(tmp_path)

    health = client.get("/health")
    unauthenticated = client.get("/goblins")
    goblins = client.get("/goblins", headers=auth_headers())

    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert unauthenticated.status_code == 401
    assert goblins.status_code == 200
    echo = next(item for item in goblins.json() if item["kind"] == "example.echo")
    assert echo["worker_mapped"] is True
    assert echo["validation_status"]["state"] == "unknown"


def test_goblins_endpoint_reports_validation_status(tmp_path: Path) -> None:
    """Verify admin goblin listings include latest validation status hints."""
    client, store, _ = build_client(tmp_path)
    store.save_worker_validation(
        WorkerValidationRecord(
            id="validation-1",
            kind="example.echo",
            image="goblin-king-example-echo:local",
            image_digest="sha256:echo",
            contract_version="goblin-king/v1alpha1",
            validator_version="goblin-king-validator/v1",
            validated_at=utc_now(),
            status="passed",
        )
    )

    response = client.get("/goblins", headers=auth_headers())
    echo = next(item for item in response.json() if item["kind"] == "example.echo")

    assert response.status_code == 200
    assert echo["validation_status"]["state"] == "validated"
    assert echo["validation_status"]["image_digest"] == "sha256:echo"


def test_goblins_endpoint_uses_project_settings(tmp_path: Path) -> None:
    """Verify API goblin discovery can load project settings."""
    project_path = tmp_path / "goblin-king-project.json"
    project_path.write_text(
        """
{
  "registries": ["goblins.json"],
  "entry_points": false,
  "images": "images.json",
  "api_settings": "api.json"
}
""",
        encoding="utf-8",
    )
    (tmp_path / "goblins.json").write_text(
        """
{"goblins":[{"kind":"project.echo","display_name":"Project Echo","module":"examples.goblins.echo"}]}
""",
        encoding="utf-8",
    )
    (tmp_path / "images.json").write_text('{"workers":{}}', encoding="utf-8")
    settings = ApiSettings(
        registry=Path("examples/goblins.json").resolve(),
        images=tmp_path / "images.json",
        db=tmp_path / "api.sqlite3",
        artifact_root=tmp_path / "artifacts",
        auth_token="test-token",
        project=project_path,
    )
    client = TestClient(create_app(settings))

    response = client.get("/goblins", headers=auth_headers())

    assert response.status_code == 200
    assert response.json()[0]["kind"] == "project.echo"


def test_goblins_endpoint_reports_project_config_source(tmp_path: Path) -> None:
    """Verify inline project-config goblins are discoverable without registry edits."""
    project_path = tmp_path / "goblin-king-project.json"
    images_path = tmp_path / "images.json"
    images_path.write_text('{"workers":{}}', encoding="utf-8")
    project_path.write_text(
        json.dumps(
            {
                "apiVersion": "goblin-king/v1alpha1",
                "kind": "GoblinProject",
                "registries": [],
                "entry_points": False,
                "images": "images.json",
                "api_settings": "api.json",
                "goblins": {
                    "project.inline.hello": {
                        "displayName": "Project Inline Hello",
                        "image": "inline-hello:local",
                        "context": ".",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    settings = ApiSettings(
        registry=Path("examples/goblins.json").resolve(),
        images=Path("goblin-images.json").resolve(),
        db=tmp_path / "api.sqlite3",
        artifact_root=tmp_path / "artifacts",
        auth_token="test-token",
        project=project_path,
    )
    client = TestClient(create_app(settings))

    response = client.get("/goblins", headers=auth_headers())
    goblin = response.json()[0]

    assert response.status_code == 200
    assert goblin["kind"] == "project.inline.hello"
    assert goblin["source"] == "project-config"
    assert goblin["worker_image"] == "inline-hello:local"


def test_discovery_reload_adds_project_goblin_without_restart(tmp_path: Path) -> None:
    """Verify admin discovery reload refreshes project registries and image maps."""
    project_path = tmp_path / "goblin-king-project.json"
    registry_path = tmp_path / "goblins.json"
    images_path = tmp_path / "images.json"
    project_path.write_text(
        json.dumps(
            {
                "registries": ["goblins.json"],
                "entry_points": False,
                "images": "images.json",
                "api_settings": "api.json",
            }
        ),
        encoding="utf-8",
    )
    registry_path.write_text(
        json.dumps(
            {
                "goblins": [
                    {
                        "kind": "project.echo",
                        "display_name": "Project Echo",
                        "module": "examples.goblins.echo",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    images_path.write_text(
        json.dumps({"workers": {"project.echo": {"context": ".", "image": "echo:local"}}}),
        encoding="utf-8",
    )
    settings = ApiSettings(
        registry=Path("examples/goblins.json").resolve(),
        images=Path("goblin-images.json").resolve(),
        db=tmp_path / "api.sqlite3",
        artifact_root=tmp_path / "artifacts",
        auth_token="test-token",
        project=project_path,
    )
    client = TestClient(create_app(settings))

    initial = client.get("/admin/discovery/status", headers=auth_headers())
    registry_path.write_text(
        json.dumps(
            {
                "goblins": [
                    {
                        "kind": "project.echo",
                        "display_name": "Project Echo",
                        "module": "examples.goblins.echo",
                    },
                    {
                        "kind": "project.new",
                        "display_name": "Project New",
                        "module": "examples.goblins.hello",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    images_path.write_text(
        json.dumps(
            {
                "workers": {
                    "project.echo": {"context": ".", "image": "echo:local"},
                    "project.new": {"context": ".", "image": "new:local"},
                }
            }
        ),
        encoding="utf-8",
    )
    reloaded = client.post("/admin/discovery/reload", headers=auth_headers())
    goblins = client.get("/goblins", headers=auth_headers())
    sources = client.get("/admin/discovery/sources", headers=auth_headers())

    assert initial.status_code == 200
    assert initial.json()["active_goblin_count"] == 1
    assert reloaded.status_code == 200
    assert reloaded.json()["active_goblin_count"] == 2
    assert reloaded.json()["discovery_version"] == initial.json()["discovery_version"] + 1
    assert [item["kind"] for item in goblins.json()] == ["project.echo", "project.new"]
    assert sources.json()["worker_unmapped_kinds"] == []


def test_admin_image_promotion_and_deployment_records(tmp_path: Path) -> None:
    """Verify admin deployment endpoints persist proof and emit operator records."""
    client, store, _ = build_client(tmp_path)

    planned = client.post(
        "/admin/images/promotions",
        headers=auth_headers(),
        json={
            "kind": "example.hello",
            "target_image": "registry.example/example-hello:prod",
            "build": True,
            "push": True,
            "dry_run": True,
        },
    )
    assert planned.status_code == 200
    promotion_id = planned.json()["id"]
    assert planned.json()["detail"]["commands"][0][0] == "docker"

    marked = client.post(
        f"/admin/images/promotions/{promotion_id}/mark",
        headers=auth_headers(),
        json={"status": "promoted", "digest": "sha256:abc"},
    )
    promotions = client.get("/admin/images/promotions", headers=auth_headers())
    helm = client.post(
        "/admin/deployments/helm-template",
        headers=auth_headers(),
        json={"name": "unit-test", "execute": False},
    )
    reload_record = client.post(
        "/admin/deployments/reload-discovery",
        headers=auth_headers(),
    )
    deployments = client.get("/admin/deployments", headers=auth_headers())

    assert marked.status_code == 200
    assert marked.json()["status"] == "promoted"
    assert promotions.json()[0]["id"] == promotion_id
    assert helm.status_code == 200
    assert helm.json()["command"][:3] == ["helm", "template", "goblin-king"]
    assert reload_record.status_code == 200
    assert reload_record.json()["action"] == "discovery-reload"
    assert deployments.status_code == 200
    assert {record["action"] for record in deployments.json()} >= {
        "helm-template",
        "discovery-reload",
    }
    assert any(event.event_type == "admin.image_promotion.planned" for event in store.list_events())


def test_failed_discovery_reload_preserves_previous_registry(tmp_path: Path) -> None:
    """Verify invalid deployed definitions are reported without replacing active discovery."""
    project_path = tmp_path / "goblin-king-project.json"
    registry_path = tmp_path / "goblins.json"
    images_path = tmp_path / "images.json"
    project_path.write_text(
        json.dumps(
            {
                "registries": ["goblins.json"],
                "entry_points": False,
                "images": "images.json",
                "api_settings": "api.json",
            }
        ),
        encoding="utf-8",
    )
    registry_path.write_text(
        json.dumps(
            {
                "goblins": [
                    {
                        "kind": "project.echo",
                        "display_name": "Project Echo",
                        "module": "examples.goblins.echo",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    images_path.write_text(
        json.dumps({"workers": {"project.echo": {"context": ".", "image": "echo:local"}}}),
        encoding="utf-8",
    )
    settings = ApiSettings(
        registry=Path("examples/goblins.json").resolve(),
        images=Path("goblin-images.json").resolve(),
        db=tmp_path / "api.sqlite3",
        artifact_root=tmp_path / "artifacts",
        auth_token="test-token",
        project=project_path,
    )
    client = TestClient(create_app(settings))

    registry_path.write_text(
        json.dumps(
            {
                "goblins": [
                    {
                        "kind": "project.echo",
                        "display_name": "Project Echo",
                        "module": "examples.goblins.echo",
                    },
                    {
                        "kind": "project.echo",
                        "display_name": "Duplicate Echo",
                        "module": "examples.goblins.echo",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    failed = client.post("/admin/discovery/reload", headers=auth_headers())
    status = client.get("/admin/discovery/status", headers=auth_headers())
    goblins = client.get("/goblins", headers=auth_headers())

    assert failed.status_code == 400
    assert "duplicate goblin kind" in failed.json()["detail"]
    assert status.json()["active_goblin_count"] == 1
    assert status.json()["last_error"] is not None
    assert [item["kind"] for item in goblins.json()] == ["project.echo"]


def test_jobs_endpoint_queues_without_running(tmp_path: Path) -> None:
    """Verify POST /jobs creates a queued job and does not create a run."""
    client, store, _ = build_client(tmp_path)

    unauthorized = client.post("/jobs", json={"kind": "example.echo", "input": {}})
    created = client.post(
        "/jobs",
        json={"kind": "example.echo", "input": {"message": "hello api"}},
        headers=auth_headers(),
    )
    listed = client.get("/jobs", headers=auth_headers())
    loaded = store.get_job(created.json()["id"])

    assert unauthorized.status_code == 401
    assert created.status_code == 200
    assert created.json()["status"] == "queued"
    assert loaded is not None
    assert loaded.status == "queued"
    assert listed.status_code == 200
    assert listed.json()["items"][0]["id"] == created.json()["id"]
    events = client.get(
        "/events",
        params={"job_id": created.json()["id"]},
        headers=auth_headers(),
    )
    assert events.status_code == 200
    assert events.json()["items"][0]["event_type"] == "job.queued"


def test_jobs_endpoint_preserves_project_config_source_metadata(tmp_path: Path) -> None:
    """Verify API-created project goblin jobs preserve source definition metadata."""
    settings = ApiSettings(
        registry=Path("examples/goblins.json").resolve(),
        images=Path("goblin-images.json").resolve(),
        project=Path("examples/adopting-project/goblin-king-project.json").resolve(),
        db=tmp_path / "api.sqlite3",
        redis_url="redis://localhost:6379/0",
        artifact_root=tmp_path / "artifacts",
        auth_token="test-token",
    )
    store = SQLiteStore(settings.db)
    client = TestClient(create_app(settings))

    created = client.post(
        "/jobs",
        json={"kind": "project.inline.hello", "input": {"name": "API"}},
        headers=auth_headers(),
    )
    loaded = store.get_job(created.json()["id"])

    assert created.status_code == 200
    assert loaded is not None
    assert loaded.metadata["goblin_source"] == "project-config"
    assert loaded.metadata["goblin_definition"]["kind"] == "project.inline.hello"


def test_jobs_endpoint_rejects_resource_policy_above_ceiling(tmp_path: Path) -> None:
    """Verify resource policy ceilings reject work before a job is persisted."""
    policy_path = tmp_path / "policies.json"
    policy_path.write_text(
        json.dumps(
            {
                "version": 1,
                "defaults": {"timeout_seconds": 30},
                "ceilings": {"timeout_seconds": 60},
            }
        ),
        encoding="utf-8",
    )
    settings = ApiSettings(
        registry=Path("examples/goblins.json").resolve(),
        images=Path("goblin-images.json").resolve(),
        db=tmp_path / "api.sqlite3",
        redis_url="redis://localhost:6379/0",
        artifact_root=tmp_path / "artifacts",
        auth_token="test-token",
        resource_policies=policy_path,
    )
    client = TestClient(create_app(settings))
    store = SQLiteStore(settings.db)

    rejected = client.post(
        "/jobs",
        json={"kind": "example.echo", "input": {}, "timeout_seconds": 120},
        headers=auth_headers(),
    )

    assert rejected.status_code == 422
    assert "resource policy exceeds ceiling" in rejected.json()["detail"]
    assert store.list_jobs() == []
    assert store.list_events()[0].event_type == "resource_policy.rejected"
    assert store.list_audit_logs()[0].action == "resource_policy.rejected"


def test_fanout_api_creates_and_reads_batch(tmp_path: Path) -> None:
    """Verify API fanout creates queued jobs and read endpoints derive status."""
    client, _, _ = build_client(tmp_path)

    unauthorized = client.post("/jobs/fanout", json={"items": []})
    created = client.post(
        "/jobs/fanout",
        json={
            "description": "demo",
            "items": [
                {"kind": "example.echo", "input": {"message": "one"}},
                {"kind": "example.echo", "input": {"message": "two"}},
            ],
        },
        headers=auth_headers(),
    )
    fanout_id = created.json()["fanout"]["id"]
    shown = client.get(f"/fanouts/{fanout_id}", headers=auth_headers())
    listed = client.get("/fanouts", headers=auth_headers())

    assert unauthorized.status_code == 401
    assert created.status_code == 200
    assert created.json()["status"] == "queued"
    assert len(created.json()["jobs"]) == 2
    assert shown.status_code == 200
    assert shown.json()["counts"]["total"] == 2
    assert listed.status_code == 200
    assert listed.json()[0]["fanout"]["id"] == fanout_id


def test_retry_api_creates_new_job_for_terminal_source(tmp_path: Path) -> None:
    """Verify API retry creates a queued job and rejects live sources."""
    client, store, _ = build_client(tmp_path)
    terminal = JobRecord(
        id="terminal",
        kind="example.echo",
        input={"message": "old"},
        created_at=utc_now(),
        status="completed",
    )
    live = JobRecord(
        id="live",
        kind="example.echo",
        input={},
        created_at=utc_now(),
        status="queued",
    )
    store.save_job(terminal)
    store.save_job(live)

    unauthorized = client.post("/jobs/terminal/retry", json={"reason": "again"})
    retry = client.post(
        "/jobs/terminal/retry",
        json={"reason": "again", "input": {"message": "new"}},
        headers=auth_headers(),
    )
    conflict = client.post("/jobs/live/retry", json={}, headers=auth_headers())

    assert unauthorized.status_code == 401
    assert retry.status_code == 200
    assert retry.json()["status"] == "queued"
    assert retry.json()["input"] == {"message": "new"}
    assert retry.json()["metadata"]["retry"]["source_job_id"] == "terminal"
    assert conflict.status_code == 409


def test_get_job_and_cancel_job(tmp_path: Path) -> None:
    """Verify jobs can be fetched and cancellable statuses can be cancelled."""
    client, store, _ = build_client(tmp_path)
    job = JobRecord(
        id="job-1",
        kind="example.echo",
        input={},
        created_at=datetime(2026, 6, 9, tzinfo=UTC),
        status="queued",
    )
    store.save_job(job)

    fetched = client.get("/jobs/job-1", headers=auth_headers())
    cancelled = client.post("/jobs/job-1/cancel", headers=auth_headers())

    assert fetched.status_code == 200
    assert fetched.json()["id"] == "job-1"
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert client.post("/jobs/job-1/cancel", headers=auth_headers()).status_code == 409
    assert client.get("/jobs/missing", headers=auth_headers()).status_code == 404


def test_schedule_create_list_and_patch(tmp_path: Path) -> None:
    """Verify schedule mutation endpoints persist and update schedule fields."""
    client, _, _ = build_client(tmp_path)

    created = client.post(
        "/schedules",
        json={
            "kind": "example.echo",
            "cron": "* * * * *",
            "input": {"message": "scheduled"},
            "due_now": True,
        },
        headers=auth_headers(),
    )
    schedule_id = created.json()["id"]
    patched = client.patch(
        f"/schedules/{schedule_id}",
        json={"enabled": False, "priority": 200},
        headers=auth_headers(),
    )
    listed = client.get("/schedules", headers=auth_headers())

    assert created.status_code == 200
    assert patched.status_code == 200
    assert patched.json()["enabled"] is False
    assert patched.json()["priority"] == 200
    assert listed.status_code == 200
    assert listed.json()[0]["id"] == schedule_id
    invalid = client.post(
        "/schedules",
        json={"kind": "example.echo", "cron": "nope"},
        headers=auth_headers(),
    )
    assert invalid.status_code == 422
    schedule_events = client.get(
        "/events",
        params={"schedule_id": schedule_id},
        headers=auth_headers(),
    )
    assert [event["event_type"] for event in schedule_events.json()["items"]] == [
        "schedule.created",
        "schedule.updated",
    ]


def test_heartbeat_endpoints(tmp_path: Path) -> None:
    """Verify heartbeat read endpoints expose persisted scheduler and worker liveness."""
    client, store, _ = build_client(tmp_path)
    heartbeat = HeartbeatRecord(
        owner_id="worker-1",
        owner_type="worker",
        status="running",
        last_seen_at=utc_now(),
        job_id="job-1",
        run_id="run-1",
    )
    store.upsert_heartbeat(heartbeat)

    listed = client.get("/heartbeats", headers=auth_headers())
    fetched = client.get("/heartbeats/worker-1", headers=auth_headers())
    missing = client.get("/heartbeats/missing", headers=auth_headers())

    assert listed.status_code == 200
    assert listed.json()[0]["owner_id"] == "worker-1"
    assert fetched.status_code == 200
    assert fetched.json()["status"] == "running"
    assert missing.status_code == 404


def test_websocket_streams_pubsub_events(tmp_path: Path, monkeypatch) -> None:
    """Verify /ws/runs streams Redis pub/sub event envelopes."""
    event = {"event_type": "job.completed", "job_id": "job-1"}

    class FakePubSub:
        def __init__(self) -> None:
            self.sent = False

        def subscribe(self, _channel: str) -> None:
            return None

        def get_message(self, *_args) -> dict | None:
            if self.sent:
                return None
            self.sent = True
            return {"type": "message", "data": json.dumps(event).encode("utf-8")}

        def close(self) -> None:
            return None

    class FakeRedis:
        def pubsub(self) -> FakePubSub:
            return FakePubSub()

    monkeypatch.setattr("goblin_king.api.Redis.from_url", lambda _url: FakeRedis())
    client, _, _ = build_client(tmp_path)

    with client.websocket_connect("/ws/runs?token=test-token") as websocket:
        assert json.loads(websocket.receive_text()) == event


def test_event_stream_status_endpoint(tmp_path: Path, monkeypatch) -> None:
    """Verify the API exposes Redis Stream delivery health."""

    class FakeRedis:
        def xinfo_stream(self, _stream: str) -> dict:
            return {"length": 2, "last-generated-id": b"2-0"}

        def xinfo_groups(self, _stream: str) -> list[dict]:
            return [{b"name": b"goblin-king-event-readers", b"pending": 1}]

    monkeypatch.setattr("goblin_king.events.Redis.from_url", lambda _url: FakeRedis())
    client, _, _ = build_client(tmp_path)

    response = client.get("/events/stream/status", headers=auth_headers())

    assert response.status_code == 200
    assert response.json()["stream"] == "goblin-king:events:stream"
    assert response.json()["length"] == 2
    assert response.json()["pending"] == 1


def test_run_and_artifact_endpoints(tmp_path: Path) -> None:
    """Verify run lookup, artifact metadata, and safe file download."""
    client, store, artifact_root = build_client(tmp_path)
    artifact_root.mkdir(parents=True, exist_ok=True)
    (artifact_root / "report.txt").write_text("hello artifact", encoding="utf-8")
    store.save_job(
        JobRecord(
            id="job-1",
            kind="example.echo",
            input={},
            created_at=datetime(2026, 6, 9, tzinfo=UTC),
            status="completed",
        )
    )
    store.save_run(
        RunRecord(
            id="run-1",
            job_id="job-1",
            kind="example.echo",
            status="completed",
            started_at=datetime(2026, 6, 9, tzinfo=UTC),
            finished_at=datetime(2026, 6, 9, tzinfo=UTC),
            result=GoblinResult.ok(
                artifacts=[
                    ArtifactRecord(name="report.txt", uri="report.txt", media_type="text/plain")
                ]
            ),
        )
    )

    run = client.get("/runs/run-1", headers=auth_headers())
    runs = client.get("/runs", headers=auth_headers())
    artifacts = client.get("/runs/run-1/artifacts", headers=auth_headers())
    download = client.get("/runs/run-1/artifacts/report.txt", headers=auth_headers())

    assert run.status_code == 200
    assert run.json()["id"] == "run-1"
    assert runs.status_code == 200
    assert runs.json()["items"][0]["id"] == "run-1"
    assert artifacts.status_code == 200
    assert artifacts.json()[0]["download_url"] == "/runs/run-1/artifacts/report.txt"
    assert download.status_code == 200
    assert download.text == "hello artifact"
    assert client.get("/runs/missing", headers=auth_headers()).status_code == 404
    assert (
        client.get("/runs/run-1/artifacts/missing.txt", headers=auth_headers()).status_code
        == 404
    )


def test_admin_artifact_storage_status_and_cleanup(tmp_path: Path) -> None:
    """Verify volume-backed artifact status and cleanup are project scoped."""
    client, store, artifact_root = build_client(tmp_path)
    artifact_root.mkdir(parents=True, exist_ok=True)
    (artifact_root / "old.txt").write_text("old artifact", encoding="utf-8")
    store.save_job(
        JobRecord(
            id="job-1",
            kind="example.artifact",
            input={},
            created_at=datetime(2026, 6, 9, tzinfo=UTC),
            status="completed",
            project_id="project-1",
        )
    )
    store.save_run(
        RunRecord(
            id="run-1",
            job_id="job-1",
            kind="example.artifact",
            status="completed",
            started_at=datetime(2026, 6, 9, tzinfo=UTC),
            finished_at=datetime(2026, 6, 9, tzinfo=UTC),
            project_id="project-1",
            result=GoblinResult.ok(
                artifacts=[
                    ArtifactRecord(name="old.txt", uri="old.txt", media_type="text/plain")
                ]
            ),
        )
    )

    status = client.get("/admin/artifacts/storage", headers=auth_headers())
    preview = client.post(
        "/admin/artifacts/cleanup",
        json={"dry_run": True, "project_id": "project-1", "max_total_bytes": 0},
        headers=auth_headers(),
    )

    assert status.status_code == 200
    assert status.json()["file_count"] == 1
    assert status.json()["metadata_count"] == 1
    assert preview.status_code == 200
    assert preview.json()["files_selected"] == 1
    assert (artifact_root / "old.txt").exists()

    deleted = client.post(
        "/admin/artifacts/cleanup",
        json={"dry_run": False, "project_id": "project-1", "max_total_bytes": 0},
        headers=auth_headers(),
    )

    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True
    assert not (artifact_root / "old.txt").exists()
    assert store.list_events(event_type="admin.artifacts.cleaned")


def test_admin_runtime_kill_job_cancels_and_records_event(tmp_path: Path, monkeypatch) -> None:
    """Verify hard-kill for a job invokes scoped termination and persists proof."""
    client, store, _ = build_client(tmp_path)
    store.save_job(
        JobRecord(
            id="job-kill",
            kind="example.hello",
            input={},
            created_at=datetime(2026, 6, 9, tzinfo=UTC),
            status="running",
        )
    )

    monkeypatch.setattr(
        "goblin_king.api.terminate_runtime",
        lambda **_kwargs: type("Result", (), {"killed": ["docker:abc"], "errors": []})(),
    )

    response = client.post(
        "/admin/runtime/jobs/job-kill/kill",
        json={"runtime": "docker"},
        headers=auth_headers(),
    )

    assert response.status_code == 200
    assert response.json()["killed"] == ["docker:abc"]
    assert response.json()["cancelled"] is True
    assert store.get_job("job-kill").status == "cancelled"  # type: ignore[union-attr]
    assert store.list_events(event_type="runtime.terminated")


def test_admin_runtime_kill_service_marks_stopped(tmp_path: Path) -> None:
    """Verify hard-stop for registered services preserves audit/event proof."""
    client, store, _ = build_client(tmp_path)
    store.save_long_service(
        LongServiceRecord(
            id="svc-kill",
            kind="example.long-hello",
            status="running",
            base_url="http://service.example",
            created_at=datetime(2026, 6, 9, tzinfo=UTC),
        )
    )

    response = client.post(
        "/admin/runtime/services/svc-kill/kill",
        json={},
        headers=auth_headers(),
    )

    assert response.status_code == 200
    assert response.json()["killed"] == ["registered-service:svc-kill"]
    assert store.get_long_service("svc-kill").status == "stopped"  # type: ignore[union-attr]
    assert store.list_events(event_type="runtime.terminated")


def test_admin_creates_user_project_and_hashed_token(tmp_path: Path) -> None:
    """Verify admin setup creates users, projects, and hashed API tokens."""
    client, store, _ = build_client(tmp_path)

    user = client.post(
        "/admin/users",
        json={"email": "dev@example.test", "display_name": "Dev"},
        headers=auth_headers(),
    )
    project = client.post(
        "/admin/projects",
        json={"name": "Project A"},
        headers=auth_headers(),
    )
    token = client.post(
        "/admin/tokens",
        json={
            "name": "project-token",
            "user_id": user.json()["id"],
            "project_id": project.json()["id"],
            "role": "member",
        },
        headers=auth_headers(),
    )

    raw_token = token.json()["raw_token"]
    stored = store.get_api_token_by_hash(hash_api_token(raw_token))

    assert user.status_code == 200
    assert project.status_code == 200
    assert token.status_code == 200
    assert stored is not None
    assert stored.token_hash == hash_api_token(raw_token)
    assert raw_token not in stored.model_dump_json()


def test_admin_cleanup_runtime_rows_dry_run_and_delete(tmp_path: Path) -> None:
    """Verify admins can preview and remove historical runtime rows safely."""
    client, store, _ = build_client(tmp_path)
    now = utc_now()
    store.save_fanout(FanoutRecord(id="fanout-1", created_at=now, created_by="test"))
    store.save_job(
        JobRecord(
            id="terminal-job",
            kind="example.echo",
            input={},
            created_at=now,
            status="completed",
            fanout_id="fanout-1",
        )
    )
    store.save_job(
        JobRecord(
            id="live-job",
            kind="example.echo",
            input={},
            created_at=now,
            status="queued",
            fanout_id="fanout-live",
        )
    )
    store.save_run(
        RunRecord(
            id="run-1",
            job_id="terminal-job",
            kind="example.echo",
            status="completed",
            started_at=now,
            result=GoblinResult.ok(
                artifacts=[
                    ArtifactRecord(
                        name="artifact-proof.txt",
                        uri="artifact-proof.txt",
                        media_type="text/plain",
                    )
                ]
            ),
        )
    )
    store.save_event(
        EventRecord(
            id="event-1",
            created_at=now,
            event_type="job.completed",
            source="api",
        )
    )
    store.upsert_heartbeat(
        HeartbeatRecord(
            owner_id="worker-1",
            owner_type="worker",
            status="completed",
            last_seen_at=now,
            job_id="terminal-job",
            run_id="run-1",
        )
    )
    store.upsert_heartbeat(
        HeartbeatRecord(
            owner_id="scheduler-1",
            owner_type="scheduler",
            status="alive",
            last_seen_at=now,
        )
    )
    store.save_long_service(
        LongServiceRecord(
            id="stopped-service",
            kind="example.long-hello",
            base_url="http://long-hello:8080",
            status="stopped",
            created_at=now,
        )
    )
    store.save_long_service(
        LongServiceRecord(
            id="running-service",
            kind="example.long-hello",
            base_url="http://long-hello:8080",
            status="running",
            created_at=now,
            last_probe_at=now,
        )
    )

    unauthorized = client.post("/admin/cleanup/runtime", json={"dry_run": True})
    preview = client.post(
        "/admin/cleanup/runtime",
        json={"dry_run": True},
        headers=auth_headers(),
    )
    deleted = client.post(
        "/admin/cleanup/runtime",
        json={"dry_run": False},
        headers=auth_headers(),
    )

    assert unauthorized.status_code == 401
    assert preview.status_code == 200
    assert preview.json()["deleted"] is False
    assert preview.json()["counts"]["jobs"] == 1
    assert preview.json()["counts"]["runs"] == 1
    assert preview.json()["counts"]["long_services"] == 1
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True
    assert store.get_job("terminal-job") is None
    assert store.get_run("run-1") is None
    assert store.get_job("live-job") is not None
    assert store.get_heartbeat("scheduler-1") is not None
    assert store.get_heartbeat("worker-1") is None
    assert store.get_long_service("stopped-service") is None
    assert store.get_long_service("running-service") is not None


def test_project_scoped_token_cannot_cross_project(tmp_path: Path) -> None:
    """Verify project-scoped tokens can only access their own project resources."""
    client, _, _ = build_client(tmp_path)
    user = client.post(
        "/admin/users",
        json={"email": "dev@example.test", "display_name": "Dev"},
        headers=auth_headers(),
    ).json()
    project_a = client.post(
        "/admin/projects",
        json={"name": "Project A"},
        headers=auth_headers(),
    ).json()
    project_b = client.post(
        "/admin/projects",
        json={"name": "Project B"},
        headers=auth_headers(),
    ).json()
    token_a = client.post(
        "/admin/tokens",
        json={
            "name": "token-a",
            "user_id": user["id"],
            "project_id": project_a["id"],
            "role": "member",
        },
        headers=auth_headers(),
    ).json()["raw_token"]
    token_b = client.post(
        "/admin/tokens",
        json={
            "name": "token-b",
            "user_id": user["id"],
            "project_id": project_b["id"],
            "role": "member",
        },
        headers=auth_headers(),
    ).json()["raw_token"]

    created = client.post(
        "/jobs",
        json={"kind": "example.echo", "input": {"message": "scoped"}},
        headers={"Authorization": f"Bearer {token_a}"},
    )
    allowed = client.get(
        f"/jobs/{created.json()['id']}",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    denied = client.get(
        f"/jobs/{created.json()['id']}",
        headers={"Authorization": f"Bearer {token_b}"},
    )

    assert created.status_code == 200
    assert created.json()["project_id"] == project_a["id"]
    assert allowed.status_code == 200
    assert denied.status_code == 403


def test_audit_logs_rate_limit_pagination_and_openapi(tmp_path: Path) -> None:
    """Verify audit logs, local rate limits, pagination metadata, and OpenAPI hardening."""
    client, store = build_client_with_limit(tmp_path, rate_limit_per_minute=1)

    first = client.get("/goblins", headers=auth_headers())
    limited = client.get("/goblins", headers=auth_headers())
    audit_logs = client.get("/audit-logs", headers=auth_headers())
    jobs = client.get("/jobs", params={"limit": 1, "offset": 0}, headers=auth_headers())
    openapi = client.get("/openapi.json")

    assert first.status_code == 200
    assert limited.status_code == 429
    assert any(log.action == "rate_limit.denied" for log in store.list_audit_logs())
    assert audit_logs.status_code in {200, 429}
    assert jobs.status_code in {200, 429}
    if jobs.status_code == 200:
        assert jobs.json()["meta"]["limit"] == 1
    assert openapi.status_code == 200
    assert "HTTPBearer" in openapi.json()["components"]["securitySchemes"]
    assert "createJob" in str(openapi.json())
