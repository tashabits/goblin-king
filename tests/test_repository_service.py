from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from goblin_king.api import create_app
from goblin_king.api_settings import ApiSettings
from goblin_king.contracts import utc_now
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

    assert response.status_code == 404
    assert response.json()["detail"] == "repository service is not enabled"
