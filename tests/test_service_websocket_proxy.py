"""Focused managed-service WebSocket proxy and drain tests."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from websockets.exceptions import ConnectionClosed
from websockets.sync.server import serve

from goblin_king.api import create_app
from goblin_king.api_settings import ApiSettings
from goblin_king.contracts import LongServiceRecord
from goblin_king.store import SQLiteStore


@contextmanager
def _websocket_server(
    seen: dict[str, Any],
    *,
    close_after_first: bool = False,
    send_on_connect: str | bytes | None = None,
) -> Iterator[str]:
    def handler(connection: Any) -> None:
        seen["path"] = connection.request.path
        seen["headers"] = {
            key.lower(): value for key, value in connection.request.headers.raw_items()
        }
        try:
            if send_on_connect is not None:
                connection.send(send_on_connect)
            for payload in connection:
                seen.setdefault("messages", []).append(payload)
                connection.send(payload)
                if close_after_first:
                    connection.close(4001, "upstream done")
                    return
        except ConnectionClosed:
            return

    server = serve(
        handler,
        "127.0.0.1",
        0,
        subprotocols=["l2l.v1"],
        select_subprotocol=(
            lambda _connection, offered: "l2l.v1" if "l2l.v1" in offered else None
        ),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.socket.getsockname()[1]}"
    finally:
        server.shutdown()
        thread.join(timeout=5)


def _client(
    tmp_path: Path,
    *,
    websocket_settings: dict[str, int | float] | None = None,
) -> tuple[TestClient, SQLiteStore]:
    settings = ApiSettings(
        registry=Path("examples/goblins.json").resolve(),
        images=Path("goblin-images.json").resolve(),
        db=tmp_path / "api.sqlite3",
        redis_url="redis://localhost:6379/0",
        artifact_root=tmp_path / "artifacts",
        auth_token="test-token",
        rate_limit_per_minute=60,
        service_websocket_proxy=websocket_settings or {},
    )
    return TestClient(create_app(settings)), SQLiteStore(settings.db)


def _save_service(
    store: SQLiteStore,
    base_url: str,
    *,
    status: str = "running",
    project_id: str | None = None,
) -> LongServiceRecord:
    service = LongServiceRecord(
        id="service-ws",
        kind="example.websocket",
        project_id=project_id,
        base_url=base_url,
        status=status,
        created_at=datetime(2026, 7, 14, tzinfo=UTC),
        last_probe_at=(datetime(2026, 7, 14, tzinfo=UTC) if status == "running" else None),
        last_probe_json=(
            {"status_code": 200, "json": {"ok": True}}
            if status == "running"
            else None
        ),
    )
    store.save_long_service(service)
    return service


def test_service_websocket_proxies_text_binary_and_safe_headers(tmp_path: Path) -> None:
    """Prove duplex frames while credentials and control query fields stay local."""
    seen: dict[str, Any] = {}
    with _websocket_server(seen) as base_url:
        client, store = _client(tmp_path)
        service = _save_service(store, base_url)

        with client.websocket_connect(
            f"/services/long-running/{service.id}/proxy/room"
            "?token=test-token&channel=blue",
            headers={
                "Cookie": "session=secret",
                "X-Api-Key": "secret",
                "X-Client-Trace": "trace-1",
            },
            subprotocols=["l2l.v1"],
        ) as websocket:
            assert websocket.accepted_subprotocol == "l2l.v1"
            websocket.send_text("hello")
            assert websocket.receive_text() == "hello"
            websocket.send_bytes(b"\x00\x01")
            assert websocket.receive_bytes() == b"\x00\x01"
            websocket.close(code=1000, reason="test complete")

        logs = _wait_for_audit(store, "service.websocket_proxy")

    assert seen["path"] == "/room?channel=blue"
    assert seen["messages"] == ["hello", b"\x00\x01"]
    assert seen["headers"]["x-client-trace"] == "trace-1"
    assert "authorization" not in seen["headers"]
    assert "cookie" not in seen["headers"]
    assert "x-api-key" not in seen["headers"]
    assert logs[-1].outcome == "success"
    assert logs[-1].detail["client_frames"] == 2
    assert logs[-1].detail["upstream_frames"] == 2


@pytest.mark.parametrize(
    ("token", "status", "expected_code"),
    [
        ("invalid", "running", 1008),
        ("test-token", "registered", 1013),
    ],
)
def test_service_websocket_rejects_unauthorized_or_unready_routes(
    tmp_path: Path,
    token: str,
    status: str,
    expected_code: int,
) -> None:
    """Require authentication and readiness before opening an upstream connection."""
    seen: dict[str, Any] = {}
    with _websocket_server(seen) as base_url:
        client, store = _client(tmp_path)
        service = _save_service(store, base_url, status=status)

        with pytest.raises(WebSocketDisconnect) as caught:
            with client.websocket_connect(
                f"/services/long-running/{service.id}/proxy?token={token}"
            ):
                pass

    assert caught.value.code == expected_code
    assert "path" not in seen


def test_service_websocket_applies_project_scope_with_header_auth(tmp_path: Path) -> None:
    """Allow the owning project and reject a valid token from a different project."""
    seen: dict[str, Any] = {}
    with _websocket_server(seen) as base_url:
        client, store = _client(tmp_path)
        project_a, token_a = _project_token(client, "A")
        _, token_b = _project_token(client, "B")
        service = _save_service(store, base_url, project_id=project_a)

        with client.websocket_connect(
            f"/services/long-running/{service.id}/proxy",
            headers={"Authorization": f"Bearer {token_a}"},
        ) as websocket:
            websocket.send_text("owned")
            assert websocket.receive_text() == "owned"
            websocket.close()

        with pytest.raises(WebSocketDisconnect) as caught:
            with client.websocket_connect(
                f"/services/long-running/{service.id}/proxy?token={token_b}"
            ):
                pass

    assert caught.value.code == 1008
    assert "authorization" not in seen["headers"]


def test_service_websocket_enforces_message_limit(tmp_path: Path) -> None:
    """Close both sides with 1009 when a client frame exceeds the fixed byte limit."""
    seen: dict[str, Any] = {}
    with _websocket_server(seen) as base_url:
        client, store = _client(tmp_path, websocket_settings={"max_message_bytes": 4})
        service = _save_service(store, base_url)

        with client.websocket_connect(
            f"/services/long-running/{service.id}/proxy?token=test-token"
        ) as websocket:
            websocket.send_text("12345")
            with pytest.raises(WebSocketDisconnect) as caught:
                websocket.receive_text()

        logs = _wait_for_audit(store, "service.websocket_proxy")

    assert caught.value.code == 1009
    assert logs[-1].outcome == "message_too_large"
    assert logs[-1].detail["close_code"] == 1009


def test_service_websocket_propagates_upstream_close(tmp_path: Path) -> None:
    """Preserve an upstream application close code and reason at the client boundary."""
    seen: dict[str, Any] = {}
    with _websocket_server(seen, close_after_first=True) as base_url:
        client, store = _client(tmp_path)
        service = _save_service(store, base_url)

        with client.websocket_connect(
            f"/services/long-running/{service.id}/proxy?token=test-token"
        ) as websocket:
            websocket.send_text("close-me")
            assert websocket.receive_text() == "close-me"
            with pytest.raises(WebSocketDisconnect) as caught:
                websocket.receive_text()

    assert caught.value.code == 4001
    assert caught.value.reason == "upstream done"


def test_service_websocket_enforces_upstream_message_limit(tmp_path: Path) -> None:
    """Apply the same message ceiling before an oversized upstream frame reaches a client."""
    seen: dict[str, Any] = {}
    with _websocket_server(seen, send_on_connect="12345") as base_url:
        client, store = _client(tmp_path, websocket_settings={"max_message_bytes": 4})
        service = _save_service(store, base_url)

        with client.websocket_connect(
            f"/services/long-running/{service.id}/proxy?token=test-token"
        ) as websocket:
            with pytest.raises(WebSocketDisconnect) as caught:
                websocket.receive_text()

    assert caught.value.code == 1009


def test_service_websocket_enforces_idle_timeout(tmp_path: Path) -> None:
    """Close an inactive duplex relay using the configured bounded idle interval."""
    seen: dict[str, Any] = {}
    with _websocket_server(seen) as base_url:
        client, store = _client(tmp_path, websocket_settings={"idle_timeout_seconds": 0.05})
        service = _save_service(store, base_url)

        with client.websocket_connect(
            f"/services/long-running/{service.id}/proxy?token=test-token"
        ) as websocket:
            with pytest.raises(WebSocketDisconnect) as caught:
                websocket.receive_text()

        logs = _wait_for_audit(store, "service.websocket_proxy")

    assert caught.value.code == 1001
    assert logs[-1].outcome == "idle_timeout"


def _wait_for_audit(store: SQLiteStore, action: str) -> list[Any]:
    for _ in range(100):
        matches = [item for item in store.list_audit_logs() if item.action == action]
        if matches:
            return matches
        time.sleep(0.01)
    raise AssertionError(f"audit action did not appear: {action}")


def _project_token(client: TestClient, suffix: str) -> tuple[str, str]:
    headers = {"Authorization": "Bearer test-token"}
    user = client.post(
        "/admin/users",
        json={
            "email": f"ws-{suffix.lower()}@example.test",
            "display_name": f"WebSocket {suffix}",
        },
        headers=headers,
    ).json()
    project = client.post(
        "/admin/projects",
        json={"name": f"WebSocket Project {suffix}"},
        headers=headers,
    ).json()
    token = client.post(
        "/admin/tokens",
        json={
            "name": f"ws-{suffix.lower()}",
            "user_id": user["id"],
            "project_id": project["id"],
            "role": "member",
        },
        headers=headers,
    ).json()["raw_token"]
    return project["id"], token
