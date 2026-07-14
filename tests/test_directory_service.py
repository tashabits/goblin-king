from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from fastapi.testclient import TestClient
from websockets.exceptions import ConnectionClosed
from websockets.sync.server import serve

from goblin_king.api import create_app
from goblin_king.api_models import LongServiceProbeResponse
from goblin_king.api_settings import ApiSettings
from goblin_king.contracts import utc_now
from goblin_king.notebook_services import NotebookServiceRuntimeProof
from goblin_king.store import SQLiteStore
from goblin_king.validation import WorkerValidationResult
from tests.api_helpers import auth_headers, build_api_client


def build_repository_api_client(tmp_path) -> tuple[TestClient, SQLiteStore]:
    artifact_root = tmp_path / "artifacts"
    settings = ApiSettings(
        registry=Path("examples/goblins.json").resolve(),
        images=Path("goblin-images.json").resolve(),
        db=tmp_path / "api.sqlite3",
        redis_url="redis://localhost:6379/0",
        artifact_root=artifact_root,
        auth_token="test-token",
        repository={"enabled": True, "url": "http://repository:8000"},
    )
    return TestClient(create_app(settings)), SQLiteStore(settings.db)


def _project_token(
    client: Any,
    *,
    email: str,
    display_name: str,
    project_name: str,
    role: str = "member",
) -> tuple[dict[str, Any], str]:
    user = client.post(
        "/admin/users",
        json={"email": email, "display_name": display_name},
        headers=auth_headers(),
    ).json()
    project = client.post(
        "/admin/projects",
        json={"name": project_name},
        headers=auth_headers(),
    ).json()
    token = client.post(
        "/admin/tokens",
        json={
            "name": f"{display_name.lower()}-token",
            "user_id": user["id"],
            "project_id": project["id"],
            "role": role,
        },
        headers=auth_headers(),
    ).json()["raw_token"]
    return project, token


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _publish_function_entry(
    client: Any,
    token: str,
    *,
    name: str,
    source: str = "def run(payload):\n    return payload\n",
) -> dict[str, Any]:
    submitted = client.post(
        "/repository/entries",
        headers=_bearer(token),
        json={
            "name": name,
            "type": "notebook_function",
            "source": source,
            "function_name": "run",
            "display_name": "Shared Function",
        },
    )
    assert submitted.status_code == 200
    entry = submitted.json()["entry"]
    client.post(
        f"/repository/entries/{entry['id']}/validate",
        headers=_bearer(token),
        json={"require_success": True},
    )
    client.post(
        f"/repository/entries/{entry['id']}/request-review",
        headers=_bearer(token),
        json={},
    )
    client.post(
        f"/repository/entries/{entry['id']}/approve",
        headers=auth_headers(),
        json={},
    )
    published = client.post(
        f"/repository/entries/{entry['id']}/publish",
        headers=auth_headers(),
        json={},
    )
    assert published.status_code == 200
    return published.json()


def _fake_repository_function_validation(**kwargs: Any) -> list[WorkerValidationResult]:
    return [
        WorkerValidationResult(
            kind=kwargs["kinds"][0],
            ok=True,
            image="goblin-king-notebook-python-function:test",
            image_digest="sha256:test",
            validated_at=utc_now(),
            result_status="success",
            checks=["fake-validation"],
        )
    ]


class _FakeRepositoryServiceManager:
    def __init__(self, *, base_url: str) -> None:
        self.base_url = base_url
        self.validated: list[str] = []
        self.started: list[str] = []
        self.started_names: list[str] = []
        self.stopped: list[tuple[str, str]] = []

    def validate(self, record: Any, *, timeout_seconds: float) -> NotebookServiceRuntimeProof:
        self.validated.append(record.kind)
        return NotebookServiceRuntimeProof(
            backend="kubernetes",
            name="repo-service-validate",
            base_url=self.base_url,
            probe={"ok": True, "timeout_seconds": timeout_seconds},
        )

    def start(
        self,
        record: Any,
        *,
        name: str | None = None,
        timeout_seconds: float,
    ) -> NotebookServiceRuntimeProof:
        self.started.append(record.kind)
        self.started_names.append(name or "repo-service-runtime")
        return NotebookServiceRuntimeProof(
            backend="kubernetes",
            name=name or "repo-service-runtime",
            base_url=self.base_url,
            probe={"ok": True, "timeout_seconds": timeout_seconds},
        )

    def stop(self, record: Any) -> dict[str, Any]:
        if record.runtime_backend and record.runtime_name:
            self.stop_by_backend(record.runtime_backend, record.runtime_name)
        return {
            "backend": record.runtime_backend,
            "name": record.runtime_name,
            "stopped": bool(record.runtime_name),
        }

    def stop_by_backend(self, backend: str, name: str) -> None:
        self.stopped.append((backend, name))


@contextmanager
def _repository_websocket_server(seen: list[str]) -> Iterator[str]:
    def handler(connection: Any) -> None:
        seen.append(connection.request.path)
        try:
            for payload in connection:
                connection.send(payload)
        except ConnectionClosed:
            return

    server = serve(handler, "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.socket.getsockname()[1]}"
    finally:
        server.shutdown()
        thread.join(timeout=5)


def _ready_probe(state: Any, service: Any) -> LongServiceProbeResponse:
    response = {"status_code": 200, "headers": {}, "json": {"ok": True}}
    updated = state.store.update_long_service_probe(
        service.id,
        status="running",
        last_probe_at=utc_now(),
        last_probe_json=response,
    )
    assert updated is not None
    return LongServiceProbeResponse(
        service=updated,
        request={"method": "GET", "url": f"{service.base_url}/hello"},
        response=response,
    )


def _publish_repository_service(
    client: TestClient,
    token: str,
    name: str,
) -> dict[str, Any]:
    submitted = client.post(
        "/repository/entries",
        headers=_bearer(token),
        json={
            "name": name,
            "type": "notebook_service",
            "source": "from fastapi import FastAPI\napp = FastAPI()\n",
            "app_name": "app",
            "requirements": ["fastapi>=0.115,<1"],
            "probe_path": "/hello",
        },
    )
    assert submitted.status_code == 200
    entry = submitted.json()["entry"]
    validated = client.post(
        f"/repository/entries/{entry['id']}/validate",
        headers=_bearer(token),
        json={"timeout_seconds": 10},
    )
    assert validated.status_code == 200
    client.post(
        f"/repository/entries/{entry['id']}/request-review",
        headers=_bearer(token),
        json={},
    )
    client.post(
        f"/repository/entries/{entry['id']}/approve",
        headers=auth_headers(),
        json={},
    )
    published = client.post(
        f"/repository/entries/{entry['id']}/publish",
        headers=auth_headers(),
        json={},
    )
    assert published.status_code == 200
    return published.json()


def _wait_for(predicate: Any) -> None:
    for _ in range(100):
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition did not become true")


def test_repository_submit_validate_review_and_publish_function(
    tmp_path,
    monkeypatch,
) -> None:
    client, store = build_repository_api_client(tmp_path)
    project, token = _project_token(
        client,
        email="bob@example.test",
        display_name="Bob",
        project_name="Shared Goblins",
    )

    def fake_validate_workers(**kwargs: Any) -> list[WorkerValidationResult]:
        kind = kwargs["kinds"][0]
        return [
            WorkerValidationResult(
                kind=kind,
                ok=True,
                image="goblin-king-notebook-python-function:test",
                image_digest="sha256:test",
                validated_at=utc_now(),
                result_status="success",
                checks=["fake-validation"],
            )
        ]

    monkeypatch.setattr("goblin_king.api.validate_workers", fake_validate_workers)

    submitted = client.post(
        "/repository/entries",
        headers=_bearer(token),
        json={
            "name": "shared.hello",
            "type": "notebook_function",
            "source": "def run(payload):\n    return {'message': payload.get('name', 'hello')}\n",
            "function_name": "run",
            "display_name": "Shared Hello",
            "description": "Reusable hello from a notebook",
            "tags": ["Demo", "hello"],
        },
    )

    assert submitted.status_code == 200
    body = submitted.json()
    entry = body["entry"]
    version = body["version"]
    assert entry["project_id"] == project["id"]
    assert entry["owner"]
    assert entry["status"] == "draft"
    assert entry["tags"] == ["demo", "hello"]
    assert version["version"] == 1
    assert version["kind"].endswith(".shared.hello.v1")
    assert store.get_notebook_goblin(version["kind"]) is not None

    hidden = client.get("/repository/entries", headers=_bearer(token))
    assert hidden.status_code == 200
    assert hidden.json()["items"] == []

    validated = client.post(
        f"/repository/entries/{entry['id']}/validate",
        headers=_bearer(token),
        json={"input": {"name": "repo"}, "require_success": True},
    )
    assert validated.status_code == 200
    assert validated.json()["version"]["status"] == "validated"
    assert validated.json()["validation"]["checks"] == ["fake-validation"]

    review = client.post(
        f"/repository/entries/{entry['id']}/request-review",
        headers=_bearer(token),
        json={"note": "ready"},
    )
    assert review.status_code == 200
    assert review.json()["entry"]["status"] == "pending_review"

    denied = client.post(
        f"/repository/entries/{entry['id']}/approve",
        headers=_bearer(token),
        json={},
    )
    assert denied.status_code == 403

    approved = client.post(
        f"/repository/entries/{entry['id']}/approve",
        headers=auth_headers(),
        json={"note": "approved"},
    )
    assert approved.status_code == 200
    assert approved.json()["versions"][0]["status"] == "approved"

    published = client.post(
        f"/repository/entries/{entry['id']}/publish",
        headers=auth_headers(),
        json={},
    )
    assert published.status_code == 200
    assert published.json()["entry"]["status"] == "published"
    assert published.json()["entry"]["published_version"] == 1

    visible = client.get("/repository/entries", headers=_bearer(token))
    assert visible.status_code == 200
    assert [item["entry"]["name"] for item in visible.json()["items"]] == ["shared.hello"]
    assert any(log.action == "repository.publish" for log in store.list_audit_logs())


def test_published_repository_function_runs_by_name_and_keeps_latest_published_version(
    tmp_path,
    monkeypatch,
) -> None:
    client, store = build_repository_api_client(tmp_path)
    project, token = _project_token(
        client,
        email="runner@example.test",
        display_name="Runner",
        project_name="Runnable Project",
    )
    monkeypatch.setattr(
        "goblin_king.api.validate_workers",
        _fake_repository_function_validation,
    )
    published = _publish_function_entry(client, token, name="shared.runner")
    entry = published["entry"]
    first_version = published["versions"][0]

    draft_v2 = client.post(
        "/repository/entries",
        headers=_bearer(token),
        json={
            "name": "shared.runner",
            "type": "notebook_function",
            "source": "def run(payload):\n    return {'v': 2, **payload}\n",
            "function_name": "run",
        },
    )
    assert draft_v2.status_code == 200
    assert draft_v2.json()["version"]["version"] == 2
    assert draft_v2.json()["entry"]["status"] == "draft"

    response = client.post(
        "/repository/functions/shared.runner/run",
        headers=_bearer(token),
        json={
            "input": {"name": "Ada"},
            "priority": 42,
            "correlation_id": "repo-run-1",
        },
    )

    assert response.status_code == 200
    body = response.json()
    job = body["job"]
    assert body["entry"]["id"] == entry["id"]
    assert body["version"]["version"] == 1
    assert job["kind"] == first_version["kind"]
    assert job["input"] == {"name": "Ada"}
    assert job["project_id"] == project["id"]
    assert job["priority"] == 42
    assert job["correlation_id"] == "repo-run-1"
    assert job["metadata"]["goblin_source"] == "repository"
    assert job["metadata"]["repository_name"] == "shared.runner"
    assert job["metadata"]["repository_version"] == 1
    assert job["metadata"]["repository_source_hash"] == first_version["source_hash"]
    saved = store.get_job(job["id"])
    assert saved is not None
    assert saved.kind == first_version["kind"]
    assert any(log.action == "repository.run" for log in store.list_audit_logs())


def test_repository_delete_draft_removes_generated_notebook_record(tmp_path) -> None:
    client, store = build_repository_api_client(tmp_path)
    _, token = _project_token(
        client,
        email="delete-draft@example.test",
        display_name="Delete Draft",
        project_name="Delete Draft Project",
    )
    submitted = client.post(
        "/repository/entries",
        headers=_bearer(token),
        json={
            "name": "shared.delete-draft",
            "type": "notebook_function",
            "source": "def run(payload):\n    return payload\n",
            "function_name": "run",
        },
    )
    assert submitted.status_code == 200
    entry = submitted.json()["entry"]
    version = submitted.json()["version"]

    denied = client.delete(f"/repository/entries/{entry['id']}", headers=_bearer(token))
    deleted = client.delete(f"/repository/entries/{entry['id']}", headers=auth_headers())

    assert denied.status_code == 403
    assert deleted.status_code == 200
    assert deleted.json() == {
        "deleted": True,
        "entry_id": entry["id"],
        "name": "shared.delete-draft",
        "status": "draft",
        "deleted_versions": 1,
        "deleted_notebook_records": 1,
    }
    assert store.get_repository_entry(entry["id"]) is None
    assert store.list_repository_versions(entry["id"]) == []
    assert store.get_notebook_goblin(version["kind"]) is None
    assert any(log.action == "repository.delete" for log in store.list_audit_logs())


def test_repository_delete_requires_published_entry_to_be_retired_first(
    tmp_path,
    monkeypatch,
) -> None:
    client, store = build_repository_api_client(tmp_path)
    _, token = _project_token(
        client,
        email="delete-published@example.test",
        display_name="Delete Published",
        project_name="Delete Published Project",
    )
    monkeypatch.setattr(
        "goblin_king.api.validate_workers",
        _fake_repository_function_validation,
    )
    published = _publish_function_entry(client, token, name="shared.delete-published")
    entry = published["entry"]

    blocked = client.delete(f"/repository/entries/{entry['id']}", headers=auth_headers())
    retired = client.post(
        f"/repository/entries/{entry['id']}/retire",
        headers=auth_headers(),
        json={},
    )
    deleted = client.delete(f"/repository/entries/{entry['id']}", headers=auth_headers())

    assert blocked.status_code == 409
    assert "retire published entries first" in blocked.json()["detail"]
    assert retired.status_code == 200
    assert retired.json()["entry"]["status"] == "retired"
    assert deleted.status_code == 200
    assert deleted.json()["status"] == "retired"
    assert store.get_repository_entry(entry["id"]) is None


def test_repository_delete_rejects_active_service_runtime(tmp_path) -> None:
    client, store = build_repository_api_client(tmp_path)
    _, token = _project_token(
        client,
        email="delete-active-service@example.test",
        display_name="Delete Active Service",
        project_name="Delete Active Service Project",
    )
    submitted = client.post(
        "/repository/entries",
        headers=_bearer(token),
        json={
            "name": "shared.delete-active-service",
            "type": "notebook_service",
            "source": "from fastapi import FastAPI\napp = FastAPI()\n",
            "app_name": "app",
        },
    )
    assert submitted.status_code == 200
    entry = submitted.json()["entry"]
    version = submitted.json()["version"]
    store.update_notebook_service_runtime(
        version["kind"],
        runtime_status="running",
        runtime_backend="kubernetes",
        runtime_name="active-service",
        active_service_id="service-1",
        updated_at=utc_now(),
    )

    blocked = client.delete(f"/repository/entries/{entry['id']}", headers=auth_headers())

    assert blocked.status_code == 409
    assert blocked.json()["detail"] == (
        "repository service is still running; stop it before deleting"
    )
    assert store.get_repository_entry(entry["id"]) is not None
    assert store.get_notebook_service(version["kind"]) is not None


def test_repository_status_all_shows_owner_drafts_without_leaking_to_other_users(
    tmp_path,
) -> None:
    client, _ = build_repository_api_client(tmp_path)
    _, owner_token = _project_token(
        client,
        email="owner-drafts@example.test",
        display_name="Owner Drafts",
        project_name="Owner Drafts Project",
    )
    _, other_token = _project_token(
        client,
        email="other-drafts@example.test",
        display_name="Other Drafts",
        project_name="Other Drafts Project",
    )
    submitted = client.post(
        "/repository/entries",
        headers=_bearer(owner_token),
        json={
            "name": "shared.owner-draft",
            "type": "notebook_function",
            "source": "def run(payload):\n    return payload\n",
            "function_name": "run",
        },
    )

    owner_list = client.get("/repository/entries?status=all", headers=_bearer(owner_token))
    other_list = client.get("/repository/entries?status=all", headers=_bearer(other_token))

    assert submitted.status_code == 200
    assert [item["entry"]["name"] for item in owner_list.json()["items"]] == [
        "shared.owner-draft"
    ]
    assert other_list.json()["items"] == []


def test_repository_function_run_rejects_unpublished_wrong_type_and_wrong_project(
    tmp_path,
    monkeypatch,
) -> None:
    client, _ = build_repository_api_client(tmp_path)
    _, token_a = _project_token(
        client,
        email="project-a-runner@example.test",
        display_name="Project A Runner",
        project_name="Project A Runner",
    )
    _, token_b = _project_token(
        client,
        email="project-b-runner@example.test",
        display_name="Project B Runner",
        project_name="Project B Runner",
    )
    monkeypatch.setattr(
        "goblin_king.api.validate_workers",
        _fake_repository_function_validation,
    )
    _publish_function_entry(client, token_a, name="shared.project-a")
    draft = client.post(
        "/repository/entries",
        headers=_bearer(token_a),
        json={
            "name": "shared.draft",
            "source": "def run(payload):\n    return payload\n",
        },
    )
    service = client.post(
        "/repository/entries",
        headers=_bearer(token_a),
        json={
            "name": "shared.service-only",
            "type": "notebook_service",
            "source": "from fastapi import FastAPI\napp = FastAPI()\n",
            "app_name": "app",
        },
    )

    unpublished = client.post(
        "/repository/functions/shared.draft/run",
        headers=_bearer(token_a),
        json={},
    )
    wrong_type = client.post(
        "/repository/functions/shared.service-only/run",
        headers=_bearer(token_a),
        json={},
    )
    wrong_project = client.post(
        "/repository/functions/shared.project-a/run",
        headers=_bearer(token_b),
        json={},
    )

    assert draft.status_code == 200
    assert service.status_code == 200
    assert unpublished.status_code == 409
    assert "no published version" in unpublished.json()["detail"]
    assert wrong_type.status_code == 409
    assert wrong_type.json()["detail"] == (
        "repository entry is notebook_service, not notebook_function"
    )
    assert wrong_project.status_code == 404


def test_repository_submit_service_creates_backing_notebook_service(tmp_path) -> None:
    client, store = build_repository_api_client(tmp_path)
    project, token = _project_token(
        client,
        email="service-author@example.test",
        display_name="Service Author",
        project_name="Service Project",
    )

    submitted = client.post(
        "/repository/entries",
        headers=_bearer(token),
        json={
            "name": "shared.long-hello",
            "type": "notebook_service",
            "source": "from fastapi import FastAPI\napp = FastAPI()\n",
            "app_name": "app",
            "requirements": ["fastapi>=0.115,<1"],
            "probe_path": "hello",
            "display_name": "Shared Long Hello",
            "tags": ["service"],
        },
    )

    assert submitted.status_code == 200
    body = submitted.json()
    version = body["version"]
    service = store.get_notebook_service(version["kind"])
    assert body["entry"]["project_id"] == project["id"]
    assert body["entry"]["type"] == "notebook_service"
    assert service is not None
    assert service.probe_path == "/hello"
    assert service.requirements == ["fastapi>=0.115,<1"]


def test_published_repository_service_lifecycle_and_proxy_by_name(
    tmp_path,
    monkeypatch,
) -> None:
    seen: dict[str, str | None] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            seen["path"] = self.path
            seen["authorization"] = self.headers.get("Authorization")
            seen["cookie"] = self.headers.get("Cookie")
            seen["api_key"] = self.headers.get("X-Api-Key")
            body = json.dumps(
                {
                    "ok": True,
                    "path": self.path,
                    "authorization": seen["authorization"],
                    "api_key": seen["api_key"],
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
    manager = _FakeRepositoryServiceManager(
        base_url=f"http://127.0.0.1:{server.server_port}"
    )
    monkeypatch.setattr(
        "goblin_king.api._notebook_service_runtime_manager",
        lambda _state: manager,
    )
    client, store = build_repository_api_client(tmp_path)
    project, token = _project_token(
        client,
        email="service-runner@example.test",
        display_name="Service Runner",
        project_name="Service Runner Project",
    )
    _, other_token = _project_token(
        client,
        email="other-service-runner@example.test",
        display_name="Other Runner",
        project_name="Other Service Project",
    )

    try:
        submitted = client.post(
            "/repository/entries",
            headers=_bearer(token),
            json={
                "name": "shared.long-hello",
                "type": "notebook_service",
                "source": "from fastapi import FastAPI\napp = FastAPI()\n",
                "app_name": "app",
                "requirements": ["fastapi>=0.115,<1"],
                "probe_path": "/hello",
            },
        )
        entry = submitted.json()["entry"]
        validated = client.post(
            f"/repository/entries/{entry['id']}/validate",
            headers=_bearer(token),
            json={"timeout_seconds": 10},
        )
        client.post(
            f"/repository/entries/{entry['id']}/request-review",
            headers=_bearer(token),
            json={},
        )
        client.post(
            f"/repository/entries/{entry['id']}/approve",
            headers=auth_headers(),
            json={},
        )
        published = client.post(
            f"/repository/entries/{entry['id']}/publish",
            headers=auth_headers(),
            json={},
        )

        started = client.post(
            "/repository/services/shared.long-hello/start",
            headers=_bearer(token),
            json={"timeout_seconds": 10},
        )
        service_id = started.json()["service"]["id"]
        proxied = client.get(
            "/repository/services/shared.long-hello/proxy/v1/items",
            params={"project_id": project["id"], "version": 1, "limit": 1},
            headers={
                "Authorization": f"Bearer {token}",
                "Cookie": "session=secret",
                "X-Api-Key": "secret",
            },
        )
        proxy_seen = dict(seen)
        probed = client.post(
            "/repository/services/shared.long-hello/probe",
            headers=_bearer(token),
            json={"version": 1},
        )
        denied = client.post(
            "/repository/services/shared.long-hello/probe",
            headers=_bearer(other_token),
            json={},
        )
        stopped = client.post(
            "/repository/services/shared.long-hello/stop",
            headers=_bearer(token),
            json={},
        )
    finally:
        server.shutdown()

    assert submitted.status_code == 200
    assert validated.status_code == 200
    assert validated.json()["validation"]["ok"] is True
    assert published.status_code == 200
    assert started.status_code == 200
    assert started.json()["entry"]["name"] == "shared.long-hello"
    assert started.json()["version"]["version"] == 1
    assert started.json()["notebook_service"]["active_service_id"] == service_id
    assert started.json()["service"]["status"] == "running"
    assert started.json()["probe"]["response"]["json"]["ok"] is True
    assert proxied.status_code == 200
    assert proxied.json()["path"] == "/v1/items?limit=1"
    assert proxy_seen == {
        "path": "/v1/items?limit=1",
        "authorization": None,
        "cookie": None,
        "api_key": None,
    }
    assert probed.status_code == 200
    assert probed.json()["notebook_service"]["active_service_id"] == service_id
    assert denied.status_code == 404
    assert stopped.status_code == 200
    assert stopped.json()["notebook_service"]["runtime_status"] == "stopped"
    assert stopped.json()["service"]["status"] == "stopped"
    assert store.get_long_service(service_id).status == "stopped"  # type: ignore[union-attr]
    stopped_record = store.get_notebook_service(started.json()["version"]["kind"])
    assert stopped_record is not None
    assert stopped_record.active_service_id is None
    assert manager.validated == [started.json()["version"]["kind"]]
    assert manager.started == [started.json()["version"]["kind"]]
    assert manager.stopped == [("kubernetes", started.json()["runtime"]["name"])]
    repository_actions = [
        log.action for log in store.list_audit_logs() if log.action.startswith("repository.")
    ]
    assert repository_actions == [
        "repository.submit",
        "repository.validate",
        "repository.review_requested",
        "repository.approve",
        "repository.publish",
        "repository.start",
        "repository.proxy",
        "repository.probe",
        "repository.stop",
    ]


def test_repository_websocket_replacement_drains_old_connection(
    tmp_path,
    monkeypatch,
) -> None:
    """Keep the old relay alive while new connections use a ready promoted runtime."""
    seen: list[str] = []
    with _repository_websocket_server(seen) as base_url:
        manager = _FakeRepositoryServiceManager(base_url=base_url)
        monkeypatch.setattr(
            "goblin_king.api._notebook_service_runtime_manager",
            lambda _state: manager,
        )
        monkeypatch.setattr("goblin_king.api._probe_long_service_record", _ready_probe)
        client, store = build_repository_api_client(tmp_path)
        project, token = _project_token(
            client,
            email="rolling@example.test",
            display_name="Rolling User",
            project_name="Rolling Project",
        )
        _publish_repository_service(client, token, "rolling.service")
        first = client.post(
            "/repository/services/rolling.service/start",
            headers=_bearer(token),
            json={"timeout_seconds": 10},
        )
        first_service_id = first.json()["service"]["id"]
        first_runtime_name = first.json()["runtime"]["name"]

        with client.websocket_connect(
            "/repository/services/rolling.service/proxy/room"
            f"?token={token}&project_id={project['id']}&version=1&channel=old"
        ) as old_connection:
            old_connection.send_text("before")
            assert old_connection.receive_text() == "before"

            second = client.post(
                "/repository/services/rolling.service/start",
                headers=_bearer(token),
                json={"timeout_seconds": 10},
            )
            second_service_id = second.json()["service"]["id"]
            assert second.status_code == 200
            assert second_service_id != first_service_id
            assert manager.stopped == []
            assert store.get_long_service(first_service_id).status == "stopped"  # type: ignore[union-attr]
            active = store.get_notebook_service(second.json()["notebook_service"]["kind"])
            assert active is not None
            assert active.active_service_id == second_service_id

            old_connection.send_text("during-drain")
            assert old_connection.receive_text() == "during-drain"
            with client.websocket_connect(
                f"/repository/services/rolling.service/proxy/new?token={token}"
            ) as new_connection:
                new_connection.send_text("new-route")
                assert new_connection.receive_text() == "new-route"
                new_connection.close()
            old_connection.close()

        _wait_for(lambda: ("kubernetes", first_runtime_name) in manager.stopped)

    assert seen == [
        "/room?channel=old",
        "/new",
    ]


def test_repository_websocket_readiness_failure_retains_last_known_good(
    tmp_path,
    monkeypatch,
) -> None:
    """Leave the prior route active when a replacement candidate fails readiness."""
    seen: list[str] = []
    probe_calls = 0

    def probe_candidate(state: Any, service: Any) -> LongServiceProbeResponse:
        nonlocal probe_calls
        probe_calls += 1
        if probe_calls == 2:
            state.store.update_long_service_probe(
                service.id,
                status="failed",
                last_probe_at=utc_now(),
                last_probe_json={"status_code": 503, "json": None},
            )
            raise HTTPException(status_code=502, detail="candidate is not ready")
        return _ready_probe(state, service)

    with _repository_websocket_server(seen) as base_url:
        manager = _FakeRepositoryServiceManager(base_url=base_url)
        monkeypatch.setattr(
            "goblin_king.api._notebook_service_runtime_manager",
            lambda _state: manager,
        )
        monkeypatch.setattr("goblin_king.api._probe_long_service_record", probe_candidate)
        client, store = build_repository_api_client(tmp_path)
        _, token = _project_token(
            client,
            email="fallback@example.test",
            display_name="Fallback User",
            project_name="Fallback Project",
        )
        _publish_repository_service(client, token, "fallback.service")
        first = client.post(
            "/repository/services/fallback.service/start",
            headers=_bearer(token),
            json={"timeout_seconds": 10},
        )
        first_service_id = first.json()["service"]["id"]

        replacement = client.post(
            "/repository/services/fallback.service/start",
            headers=_bearer(token),
            json={"timeout_seconds": 10},
        )

        assert replacement.status_code == 502
        record = store.get_notebook_service(first.json()["notebook_service"]["kind"])
        assert record is not None
        assert record.active_service_id == first_service_id
        assert store.get_long_service(first_service_id).status == "running"  # type: ignore[union-attr]
        assert manager.stopped == [("kubernetes", manager.started_names[-1])]
        assert manager.started_names[-1] != manager.started_names[0]

        with client.websocket_connect(
            f"/repository/services/fallback.service/proxy?token={token}"
        ) as connection:
            connection.send_text("last-known-good")
            assert connection.receive_text() == "last-known-good"
            connection.close()

    assert seen == ["/"]


def test_repository_list_search_filters_status_type_and_tag_text(
    tmp_path,
    monkeypatch,
) -> None:
    client, _ = build_repository_api_client(tmp_path)
    _, token = _project_token(
        client,
        email="search@example.test",
        display_name="Search",
        project_name="Search Project",
    )

    def fake_validate_workers(**kwargs: Any) -> list[WorkerValidationResult]:
        return [
            WorkerValidationResult(
                kind=kwargs["kinds"][0],
                ok=True,
                image="image",
                image_digest="digest",
                validated_at=utc_now(),
            )
        ]

    monkeypatch.setattr("goblin_king.api.validate_workers", fake_validate_workers)
    published_entry = client.post(
        "/repository/entries",
        headers=_bearer(token),
        json={
            "name": "analytics.summary",
            "type": "notebook_function",
            "source": "def run(payload):\n    return payload\n",
            "tags": ["curated", "reports"],
        },
    ).json()["entry"]
    client.post(
        f"/repository/entries/{published_entry['id']}/validate",
        headers=_bearer(token),
        json={},
    )
    client.post(
        f"/repository/entries/{published_entry['id']}/request-review",
        headers=_bearer(token),
        json={},
    )
    client.post(
        f"/repository/entries/{published_entry['id']}/approve",
        headers=auth_headers(),
        json={},
    )
    client.post(
        f"/repository/entries/{published_entry['id']}/publish",
        headers=auth_headers(),
        json={},
    )
    client.post(
        "/repository/entries",
        headers=_bearer(token),
        json={
            "name": "analytics.draft",
            "type": "notebook_function",
            "source": "def run(payload):\n    return payload\n",
            "tags": ["curated"],
        },
    )

    response = client.get(
        "/repository/entries",
        headers=_bearer(token),
        params={
            "q": "curated",
            "type": "notebook_function",
            "status": "published",
            "limit": 10,
            "offset": 0,
        },
    )

    assert response.status_code == 200
    assert response.json()["meta"] == {"limit": 10, "offset": 0, "count": 1}
    assert [
        (item["entry"]["name"], item["entry"]["type"])
        for item in response.json()["items"]
    ] == [("analytics.summary", "notebook_function")]

    owner_all = client.get(
        "/repository/entries",
        headers=_bearer(token),
        params={"status": "all", "limit": 10, "offset": 0},
    )

    assert owner_all.status_code == 200
    assert [item["entry"]["name"] for item in owner_all.json()["items"]] == [
        "analytics.draft",
        "analytics.summary",
    ]


def test_repository_project_scoping_hides_other_project_entries(tmp_path, monkeypatch) -> None:
    client, _ = build_repository_api_client(tmp_path)
    _, token_a = _project_token(
        client,
        email="bob-a@example.test",
        display_name="Bob A",
        project_name="Project A",
    )
    _, token_b = _project_token(
        client,
        email="carol-b@example.test",
        display_name="Carol B",
        project_name="Project B",
    )

    def fake_validate_workers(**kwargs: Any) -> list[WorkerValidationResult]:
        return [
            WorkerValidationResult(
                kind=kwargs["kinds"][0],
                ok=True,
                image="image",
                image_digest="digest",
                validated_at=utc_now(),
            )
        ]

    monkeypatch.setattr("goblin_king.api.validate_workers", fake_validate_workers)
    entry = client.post(
        "/repository/entries",
        headers=_bearer(token_a),
        json={
            "name": "project-a.only",
            "source": "def run(payload):\n    return payload\n",
        },
    ).json()["entry"]
    client.post(
        f"/repository/entries/{entry['id']}/validate",
        headers=_bearer(token_a),
        json={},
    )
    client.post(
        f"/repository/entries/{entry['id']}/request-review",
        headers=_bearer(token_a),
        json={},
    )
    client.post(
        f"/repository/entries/{entry['id']}/approve",
        headers=auth_headers(),
        json={},
    )
    client.post(
        f"/repository/entries/{entry['id']}/publish",
        headers=auth_headers(),
        json={},
    )

    visible_to_owner = client.get("/repository/entries", headers=_bearer(token_a))
    hidden_from_other_project = client.get("/repository/entries", headers=_bearer(token_b))

    assert visible_to_owner.status_code == 200
    assert [item["entry"]["name"] for item in visible_to_owner.json()["items"]] == [
        "project-a.only"
    ]
    assert hidden_from_other_project.status_code == 200
    assert hidden_from_other_project.json()["items"] == []


def test_repository_reject_requires_admin_role(tmp_path, monkeypatch) -> None:
    client, store = build_repository_api_client(tmp_path)
    project, token = _project_token(
        client,
        email="reviewer@example.test",
        display_name="Reviewer",
        project_name="Review Project",
    )

    def fake_validate_workers(**kwargs: Any) -> list[WorkerValidationResult]:
        return [
            WorkerValidationResult(
                kind=kwargs["kinds"][0],
                ok=True,
                image="image",
                image_digest="digest",
                validated_at=utc_now(),
            )
        ]

    monkeypatch.setattr("goblin_king.api.validate_workers", fake_validate_workers)
    entry = client.post(
        "/repository/entries",
        headers=_bearer(token),
        json={
            "name": "review.only",
            "source": "def run(payload):\n    return payload\n",
        },
    ).json()["entry"]
    client.post(
        f"/repository/entries/{entry['id']}/validate",
        headers=_bearer(token),
        json={},
    )
    client.post(
        f"/repository/entries/{entry['id']}/request-review",
        headers=_bearer(token),
        json={"note": "ready"},
    )

    denied = client.post(
        f"/repository/entries/{entry['id']}/reject",
        headers=_bearer(token),
        json={"note": "needs changes"},
    )
    rejected = client.post(
        f"/repository/entries/{entry['id']}/reject",
        headers=auth_headers(),
        json={"note": "needs changes"},
    )

    assert denied.status_code == 403
    assert rejected.status_code == 200
    assert rejected.json()["versions"][0]["status"] == "rejected"
    reject_logs = [
        log
        for log in store.list_audit_logs(project_id=project["id"])
        if log.action == "repository.reject"
    ]
    assert reject_logs[0].detail["note"] == "needs changes"


def test_repository_routes_are_disabled_by_default(tmp_path) -> None:
    client, _, _ = build_api_client(tmp_path)

    response = client.get("/repository/entries", headers=auth_headers())
    run_response = client.post(
        "/repository/functions/shared.hello/run",
        headers=auth_headers(),
        json={},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "repository service is not enabled"
    assert run_response.status_code == 404
    assert run_response.json()["detail"] == "repository service is not enabled"
