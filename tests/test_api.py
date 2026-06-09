"""Local HTTP tests for the Phase 4 FastAPI control plane."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from goblin_king.api import create_app
from goblin_king.api_settings import ApiSettings
from goblin_king.contracts import (
    ArtifactRecord,
    GoblinResult,
    HeartbeatRecord,
    JobRecord,
    RunRecord,
    utc_now,
)
from goblin_king.store import SQLiteStore


def build_client(tmp_path: Path) -> tuple[TestClient, SQLiteStore, Path]:
    """Create a test API app with isolated settings and SQLite state."""
    artifact_root = tmp_path / "artifacts"
    settings = ApiSettings(
        registry=Path("examples/goblins.json").resolve(),
        images=Path("goblin-images.json").resolve(),
        db=tmp_path / "api.sqlite3",
        redis_url="redis://localhost:6379/0",
        artifact_root=artifact_root,
        auth_token="test-token",
    )
    return TestClient(create_app(settings)), SQLiteStore(settings.db), artifact_root


def auth_headers() -> dict[str, str]:
    """Return the static bearer token used by test settings."""
    return {"Authorization": "Bearer test-token"}


def test_health_and_goblins_endpoints(tmp_path: Path) -> None:
    """Verify read endpoints expose health and registry/image mapping data."""
    client, _, _ = build_client(tmp_path)

    health = client.get("/health")
    goblins = client.get("/goblins")

    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert goblins.status_code == 200
    assert goblins.json()[0]["kind"] == "example.echo"
    assert goblins.json()[0]["worker_mapped"] is True


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

    response = client.get("/goblins")

    assert response.status_code == 200
    assert response.json()[0]["kind"] == "project.echo"


def test_jobs_endpoint_queues_without_running(tmp_path: Path) -> None:
    """Verify POST /jobs creates a queued job and does not create a run."""
    client, store, _ = build_client(tmp_path)

    unauthorized = client.post("/jobs", json={"kind": "example.echo", "input": {}})
    created = client.post(
        "/jobs",
        json={"kind": "example.echo", "input": {"message": "hello api"}},
        headers=auth_headers(),
    )
    listed = client.get("/jobs")
    loaded = store.get_job(created.json()["id"])

    assert unauthorized.status_code == 401
    assert created.status_code == 200
    assert created.json()["status"] == "queued"
    assert loaded is not None
    assert loaded.status == "queued"
    assert listed.status_code == 200
    assert listed.json()[0]["id"] == created.json()["id"]
    events = client.get("/events", params={"job_id": created.json()["id"]})
    assert events.status_code == 200
    assert events.json()[0]["event_type"] == "job.queued"


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
    shown = client.get(f"/fanouts/{fanout_id}")
    listed = client.get("/fanouts")

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

    fetched = client.get("/jobs/job-1")
    cancelled = client.post("/jobs/job-1/cancel", headers=auth_headers())

    assert fetched.status_code == 200
    assert fetched.json()["id"] == "job-1"
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert client.post("/jobs/job-1/cancel", headers=auth_headers()).status_code == 409
    assert client.get("/jobs/missing").status_code == 404


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
    listed = client.get("/schedules")

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
    schedule_events = client.get("/events", params={"schedule_id": schedule_id})
    assert [event["event_type"] for event in schedule_events.json()] == [
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

    listed = client.get("/heartbeats")
    fetched = client.get("/heartbeats/worker-1")
    missing = client.get("/heartbeats/missing")

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

    with client.websocket_connect("/ws/runs") as websocket:
        assert json.loads(websocket.receive_text()) == event


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

    run = client.get("/runs/run-1")
    artifacts = client.get("/runs/run-1/artifacts")
    download = client.get("/runs/run-1/artifacts/report.txt")

    assert run.status_code == 200
    assert run.json()["id"] == "run-1"
    assert artifacts.status_code == 200
    assert artifacts.json()[0]["download_url"] == "/runs/run-1/artifacts/report.txt"
    assert download.status_code == 200
    assert download.text == "hello artifact"
    assert client.get("/runs/missing").status_code == 404
    assert client.get("/runs/run-1/artifacts/missing.txt").status_code == 404
