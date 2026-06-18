from __future__ import annotations

import json
from io import BytesIO
from urllib.error import HTTPError

import pytest

from goblin_king.jupyter_directory import (
    DIRECTORY_API_PREFIX,
    DirectoryProxyError,
    _directory_base_url,
    _forward_directory_request,
    _hub_user_token,
    _jupyter_server_extension_points,
)


class _FakeResponse:
    def __init__(
        self,
        payload: dict[str, object],
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status = status
        self.headers = headers or {"content-type": "application/json"}
        self._payload = payload

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


def test_jupyter_server_extension_advertises_directory_module() -> None:
    assert _jupyter_server_extension_points() == [
        {"module": "goblin_king.jupyter_directory"}
    ]
    assert DIRECTORY_API_PREFIX == "/goblin-directory/api"


def test_directory_url_prefers_directory_env_then_falls_back() -> None:
    environ = {
        "GOBLIN_KING_API_URL": "http://api:8000/",
        "GOBLIN_KING_REPOSITORY_URL": "http://compat:8000/",
        "GOBLIN_KING_DIRECTORY_URL": "http://directory:8000/",
    }

    assert _directory_base_url(environ) == "http://directory:8000"

    del environ["GOBLIN_KING_DIRECTORY_URL"]
    assert _directory_base_url(environ) == "http://compat:8000"

    del environ["GOBLIN_KING_REPOSITORY_URL"]
    assert _directory_base_url(environ) == "http://api:8000"


def test_missing_hub_token_and_directory_url_are_actionable() -> None:
    with pytest.raises(DirectoryProxyError, match="JUPYTERHUB_API_TOKEN"):
        _hub_user_token({})
    with pytest.raises(DirectoryProxyError, match="GOBLIN_KING_DIRECTORY_URL"):
        _directory_base_url({})


def test_forward_list_entries_uses_user_token_and_strips_sensitive_headers() -> None:
    seen: dict[str, object] = {}

    def fake_urlopen(request, timeout):
        seen["url"] = request.full_url
        seen["headers"] = dict(request.header_items())
        seen["timeout"] = timeout
        return _FakeResponse(
            {"items": []},
            headers={
                "content-type": "application/json",
                "set-cookie": "secret=1",
                "content-length": "12",
                "x-proof": "ok",
            },
        )

    response = _forward_directory_request(
        "GET",
        "/repository/entries",
        query="status=published&type=notebook_function",
        environ={
            "JUPYTERHUB_API_TOKEN": "user-token",
            "GOBLIN_KING_DIRECTORY_URL": "http://directory.example",
        },
        opener=fake_urlopen,
        timeout_seconds=9,
    )

    assert seen["url"] == (
        "http://directory.example/repository/entries?"
        "status=published&type=notebook_function"
    )
    assert seen["headers"]["Authorization"] == "Bearer user-token"
    assert seen["timeout"] == 9
    assert response.status_code == 200
    assert response.headers == {"content-type": "application/json", "x-proof": "ok"}


def test_forward_function_and_service_requests_preserve_json_body() -> None:
    seen: list[tuple[str, str, bytes | None, dict[str, str]]] = []

    def fake_urlopen(request, timeout):
        assert timeout == 120.0
        seen.append(
            (
                request.get_method(),
                request.full_url,
                request.data,
                dict(request.header_items()),
            )
        )
        return _FakeResponse({"ok": True})

    environ = {
        "JUPYTERHUB_API_TOKEN": "carol-token",
        "GOBLIN_KING_DIRECTORY_URL": "http://directory.example",
    }
    body = json.dumps({"input": {"name": "Carol"}}).encode("utf-8")

    _forward_directory_request(
        "POST",
        "/repository/functions/shared.hello/run",
        body=body,
        content_type="application/json",
        environ=environ,
        opener=fake_urlopen,
    )
    _forward_directory_request(
        "POST",
        "/repository/services/shared.long-hello/start",
        body=b"{}",
        content_type="application/json",
        environ=environ,
        opener=fake_urlopen,
    )

    assert seen == [
        (
            "POST",
            "http://directory.example/repository/functions/shared.hello/run",
            body,
            {
                "Accept": "application/json",
                "Authorization": "Bearer carol-token",
                "Content-type": "application/json",
            },
        ),
        (
            "POST",
            "http://directory.example/repository/services/shared.long-hello/start",
            b"{}",
            {
                "Accept": "application/json",
                "Authorization": "Bearer carol-token",
                "Content-type": "application/json",
            },
        ),
    ]


def test_forward_returns_upstream_authorization_failures() -> None:
    def fake_urlopen(_request, timeout):
        assert timeout == 120.0
        raise HTTPError(
            url="http://directory.example/repository/entries",
            code=403,
            msg="Forbidden",
            hdrs={"content-type": "application/json"},
            fp=BytesIO(b'{"detail":"not allowed"}'),
        )

    response = _forward_directory_request(
        "GET",
        "/repository/entries",
        environ={
            "JUPYTERHUB_API_TOKEN": "mallory-token",
            "GOBLIN_KING_DIRECTORY_URL": "http://directory.example",
        },
        opener=fake_urlopen,
    )

    assert response.status_code == 403
    assert json.loads(response.body) == {"detail": "not allowed"}


def test_forward_unreachable_directory_api_is_actionable() -> None:
    def fake_urlopen(_request, timeout):
        assert timeout == 120.0
        raise OSError("connection refused")

    with pytest.raises(DirectoryProxyError, match="Goblin Directory API is unreachable"):
        _forward_directory_request(
            "GET",
            "/repository/entries",
            environ={
                "JUPYTERHUB_API_TOKEN": "user-token",
                "GOBLIN_KING_DIRECTORY_URL": "http://directory.example",
            },
            opener=fake_urlopen,
        )
