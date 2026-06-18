from __future__ import annotations

import json
import zipfile
from io import BytesIO
from typing import Any

from fastapi.testclient import TestClient

from goblin_king.directory_ui import (
    DirectoryUISessionStore,
    DirectoryUISettings,
    _encode_signed_json,
    _signed_value,
    create_directory_ui_app,
)


def _settings(**overrides: object) -> DirectoryUISettings:
    payload = {
        "api_url": "http://api.example",
        "repository_url": "http://repository.example",
        "hub_api_url": "http://hub.example/hub/api",
        "hub_base_url": "/hub/",
        "service_token": "service-secret",
        "service_prefix": "/services/goblin-directory/",
        "admin_groups": ["goblin-admins"],
    }
    payload.update(overrides)
    return DirectoryUISettings.model_validate(payload)


def _client(
    settings: DirectoryUISettings | None = None,
    store: DirectoryUISessionStore | None = None,
) -> tuple[TestClient, DirectoryUISettings, DirectoryUISessionStore]:
    settings = settings or _settings()
    store = store or DirectoryUISessionStore()
    return TestClient(create_directory_ui_app(settings, session_store=store)), settings, store


def _set_session_cookie(
    client: TestClient,
    settings: DirectoryUISettings,
    store: DirectoryUISessionStore,
    *,
    user_name: str = "bob",
    token: str = "hub-user-token",
    groups: list[str] | None = None,
    is_admin: bool = False,
) -> None:
    session = store.create(
        user_name=user_name,
        token=token,
        groups=groups or ["goblin-users"],
        is_admin=is_admin,
        ttl_seconds=600,
    )
    client.cookies.set(
        settings.session_cookie_name,
        _signed_value(session.session_id, settings),
        path=settings.normalized_prefix,
    )


def _bundle() -> bytes:
    manifest = {
        "schema_version": 1,
        "name": "shared.hello",
        "type": "notebook_function",
        "entrypoint": "hello.py",
        "function_name": "run",
    }
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("goblin-directory.json", json.dumps(manifest))
        archive.writestr("hello.py", "def run(payload):\n    return payload\n")
    return buffer.getvalue()


class _FakeResponse:
    def __init__(
        self,
        payload: dict[str, Any] | list[Any],
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self._payload = payload
        self.status = status
        self.headers = headers or {"content-type": "application/json"}

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


def test_browser_request_without_session_redirects_to_hub_oauth() -> None:
    client, _, _ = _client()

    response = client.get("/services/goblin-directory/", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"].startswith("/hub/api/oauth2/authorize?")
    assert "client_id=service-goblin-directory" in response.headers["location"]
    assert "goblin_directory_oauth_state" in response.headers["set-cookie"]


def test_oauth_callback_exchanges_code_identifies_user_and_sets_session(monkeypatch) -> None:
    client, settings, _ = _client()
    calls: list[str] = []

    def fake_urlopen(request: Any, timeout: float) -> _FakeResponse:
        calls.append(request.full_url)
        if request.full_url.endswith("/oauth2/token"):
            assert b"grant_type=authorization_code" in request.data
            return _FakeResponse({"access_token": "oauth-token"})
        if request.full_url.endswith("/user"):
            return _FakeResponse({"name": "alice"})
        if request.full_url.endswith("/users/alice"):
            return _FakeResponse({"name": "alice", "groups": [{"name": "goblin-admins"}]})
        raise AssertionError(request.full_url)

    monkeypatch.setattr("goblin_king.directory_ui.urlrequest.urlopen", fake_urlopen)
    client.cookies.set(
        settings.state_cookie_name,
        _encode_signed_json(
            {"state": "state-1", "next": "/done", "expires_at": 9_999_999_999},
            settings,
        ),
        path=settings.normalized_prefix,
    )

    response = client.get(
        "/services/goblin-directory/oauth_callback?code=abc&state=state-1",
        follow_redirects=False,
    )

    assert response.status_code == 307
    assert response.headers["location"] == "/done"
    assert settings.session_cookie_name in response.headers["set-cookie"]
    assert calls == [
        "http://hub.example/hub/api/oauth2/token",
        "http://hub.example/hub/api/user",
        "http://hub.example/hub/api/users/alice",
    ]


def test_me_reports_signed_in_user_and_admin_status() -> None:
    client, settings, store = _client()
    _set_session_cookie(
        client,
        settings,
        store,
        user_name="alice",
        groups=["goblin-users", "goblin-admins"],
        is_admin=True,
    )

    response = client.get("/services/goblin-directory/ui-api/me")

    assert response.status_code == 200
    assert response.json()["user"] == "alice"
    assert response.json()["is_admin"] is True


def test_bundle_preview_and_submit_are_authenticated_and_forwarded(monkeypatch) -> None:
    client, settings, store = _client()
    _set_session_cookie(client, settings, store)
    forwarded: dict[str, Any] = {}

    def fake_urlopen(request: Any, timeout: float) -> _FakeResponse:
        forwarded["url"] = request.full_url
        forwarded["headers"] = dict(request.header_items())
        forwarded["payload"] = json.loads(request.data.decode("utf-8"))
        return _FakeResponse({"entry": {"name": "shared.hello"}, "version": {}, "notebook": {}})

    monkeypatch.setattr("goblin_king.directory_ui.urlrequest.urlopen", fake_urlopen)

    preview = client.post(
        "/services/goblin-directory/ui-api/bundles/preview",
        content=_bundle(),
        headers={"Authorization": "Bearer browser-token", "Content-Type": "application/zip"},
    )
    submitted = client.post(
        "/services/goblin-directory/ui-api/bundles/submit",
        content=_bundle(),
        headers={"Authorization": "Bearer browser-token", "Content-Type": "application/zip"},
    )

    assert preview.status_code == 200
    assert submitted.status_code == 200
    assert forwarded["url"] == "http://repository.example/repository/entries"
    assert forwarded["headers"]["Authorization"] == "Bearer hub-user-token"
    assert forwarded["payload"]["name"] == "shared.hello"


def test_proxy_allows_directory_jobs_and_runs_but_not_admin_tokens(monkeypatch) -> None:
    client, settings, store = _client()
    _set_session_cookie(client, settings, store)
    urls: list[str] = []

    def fake_urlopen(request: Any, timeout: float) -> _FakeResponse:
        urls.append(request.full_url)
        return _FakeResponse({"items": [], "meta": {"count": 0, "limit": 50, "offset": 0}})

    monkeypatch.setattr("goblin_king.directory_ui.urlrequest.urlopen", fake_urlopen)

    allowed = client.get("/services/goblin-directory/ui-api/directory/entries?status=published")
    sibling = client.get("/services/goblin-directory/ui-api/jobs-extra")
    denied = client.get("/services/goblin-directory/ui-api/admin/tokens")

    assert allowed.status_code == 200
    assert sibling.status_code == 404
    assert denied.status_code == 404
    assert urls == ["http://repository.example/repository/entries?status=published"]
