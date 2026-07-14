"""Bounded relay transport and rolling-drain unit tests."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import WebSocket
from pydantic import ValidationError
from starlette.datastructures import Headers

from goblin_king.api_settings import ApiSettings
from goblin_king.service_websocket_drain import WebSocketDrainRegistry
from goblin_king.service_websocket_proxy import (
    ServiceWebSocketProxyConfig,
    filtered_query_string,
    proxy_managed_service_websocket,
    service_websocket_url,
)


@pytest.mark.asyncio
async def test_proxy_uses_bounded_buffers_and_awaits_upstream_backpressure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Configure bounded library queues and stop reading while an upstream send blocks."""
    send_started = asyncio.Event()
    release_send = asyncio.Event()
    never_receive = asyncio.Event()
    captured: dict[str, Any] = {}

    class FakeDownstream:
        headers = Headers()
        receive_calls = 0

        async def accept(self, *, subprotocol: str | None = None) -> None:
            captured["accepted_subprotocol"] = subprotocol

        async def receive(self) -> dict[str, Any]:
            self.receive_calls += 1
            if self.receive_calls == 1:
                return {"type": "websocket.receive", "text": "bounded"}
            return {"type": "websocket.disconnect", "code": 1000, "reason": "done"}

        async def close(self, *, code: int, reason: str) -> None:
            captured["downstream_close"] = (code, reason)

    class FakeUpstream:
        subprotocol = None

        async def send(self, payload: str | bytes) -> None:
            captured["payload"] = payload
            send_started.set()
            await release_send.wait()

        async def recv(self) -> str:
            await never_receive.wait()
            return "unreachable"

        async def close(self, *, code: int, reason: str) -> None:
            captured["upstream_close"] = (code, reason)

    upstream = FakeUpstream()

    class FakeConnection:
        async def __aenter__(self) -> FakeUpstream:
            return upstream

        async def __aexit__(self, *args: Any) -> None:
            return None

    def fake_connect(
        uri: str,
        *,
        origin: str | None,
        subprotocols: list[str] | None,
        compression: str,
        open_timeout: float,
        ping_interval: float,
        ping_timeout: float,
        close_timeout: float,
        max_size: int,
        max_queue: int,
        write_limit: int,
        additional_headers: list[tuple[str, str]],
        proxy: None,
    ) -> FakeConnection:
        captured.update(
            {
                "uri": uri,
                "max_size": max_size,
                "max_queue": max_queue,
                "write_limit": write_limit,
                "additional_headers": additional_headers,
            }
        )
        return FakeConnection()

    monkeypatch.setattr(
        "goblin_king.service_websocket_proxy.websockets.connect",
        fake_connect,
    )
    downstream = FakeDownstream()
    config = ServiceWebSocketProxyConfig(
        max_message_bytes=2048,
        max_queue_messages=3,
        write_limit_bytes=4096,
    )

    relay = asyncio.create_task(
        proxy_managed_service_websocket(
            downstream,  # type: ignore[arg-type]
            upstream_url="ws://service.example/socket",
            config=config,
        )
    )
    await asyncio.wait_for(send_started.wait(), timeout=1)
    await asyncio.sleep(0)
    assert downstream.receive_calls == 1
    release_send.set()
    result = await asyncio.wait_for(relay, timeout=1)

    assert result.outcome == "client_closed"
    assert captured["payload"] == "bounded"
    assert captured["max_size"] == 2048
    assert captured["max_queue"] == 3
    assert captured["write_limit"] == 4096


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_message_bytes", 64 * 1024 * 1024 + 1),
        ("max_queue_messages", 1025),
        ("write_limit_bytes", 16 * 1024 * 1024 + 1),
        ("open_timeout_seconds", 301),
        ("idle_timeout_seconds", 86401),
        ("close_timeout_seconds", 301),
        ("drain_timeout_seconds", 3601),
    ],
)
def test_service_websocket_settings_reject_effectively_unbounded_values(
    field: str,
    value: int,
) -> None:
    """Keep every operator-configurable relay resource inside a finite safety ceiling."""
    with pytest.raises(ValidationError):
        ApiSettings(service_websocket_proxy={field: value})


@pytest.mark.asyncio
async def test_drain_rejects_new_connections_and_retires_after_last_release() -> None:
    """Keep an old connection alive, reject new ones, then retire after release."""
    registry = WebSocketDrainRegistry(drain_timeout_seconds=1)
    first = AsyncMock(spec=WebSocket)
    second = AsyncMock(spec=WebSocket)
    retired: list[str] = []

    assert await registry.register("old", first) is True
    await registry.drain("old", lambda: _append_async(retired, "old"))
    assert await registry.register("old", second) is False
    assert retired == []

    await registry.unregister("old", first)

    assert retired == ["old"]


@pytest.mark.asyncio
async def test_drain_timeout_closes_connection_before_retirement() -> None:
    """Bound a stuck rolling drain and visibly close its remaining client."""
    registry = WebSocketDrainRegistry(drain_timeout_seconds=0.01)
    websocket = AsyncMock(spec=WebSocket)
    retired: list[str] = []
    assert await registry.register("old", websocket) is True

    await registry.drain("old", lambda: _append_async(retired, "old"))
    await _wait_for(lambda: bool(retired))

    websocket.close.assert_awaited_once_with(
        code=1012,
        reason="managed service replaced",
    )
    assert retired == ["old"]


def test_websocket_url_and_query_filter_reject_credentials() -> None:
    """Keep client tokens and registered URL credentials outside the upstream URL."""
    assert service_websocket_url(
        "https://service.example/base/",
        "room one",
        filtered_query_string(
            "token=secret&project_id=p1&room=blue",
            excluded={"token", "project_id"},
        ),
    ) == "wss://service.example/base/room%20one?room=blue"
    with pytest.raises(ValueError, match="must not contain credentials"):
        service_websocket_url("https://user:secret@service.example", "", "")


async def _append_async(values: list[str], value: str) -> None:
    values.append(value)


async def _wait_for(predicate: Any) -> None:
    for _ in range(100):
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition did not become true")
