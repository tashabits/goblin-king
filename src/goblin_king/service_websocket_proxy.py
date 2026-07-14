"""Bounded WebSocket relay and connection draining for managed services."""

from __future__ import annotations

import asyncio
import inspect
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any
from urllib import parse as urlparse

import websockets
from fastapi import WebSocket
from websockets.exceptions import ConnectionClosed

_SENSITIVE_HEADERS = {
    "authorization",
    "cookie",
    "host",
    "proxy-authorization",
    "x-api-key",
    "x-auth-token",
}
_HANDSHAKE_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "sec-websocket-accept",
    "sec-websocket-extensions",
    "sec-websocket-key",
    "sec-websocket-protocol",
    "sec-websocket-version",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}
_SUBPROTOCOL = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
_VALID_CLOSE_CODES = set(range(1000, 1015)) - {1004, 1005, 1006}
_VALID_CLOSE_CODES.update(range(3000, 5000))


@dataclass(frozen=True)
class ServiceWebSocketProxyConfig:
    """Operator-controlled limits applied to every managed-service relay."""

    max_message_bytes: int = 1024 * 1024
    max_queue_messages: int = 16
    write_limit_bytes: int = 32 * 1024
    open_timeout_seconds: float = 10.0
    idle_timeout_seconds: float = 300.0
    close_timeout_seconds: float = 10.0
    drain_timeout_seconds: float = 30.0


@dataclass(frozen=True)
class ServiceWebSocketProxyResult:
    """Observable terminal outcome and bounded traffic counters for one relay."""

    outcome: str
    close_code: int
    reason: str
    client_frames: int
    client_bytes: int
    upstream_frames: int
    upstream_bytes: int


class ServiceWebSocketProxyError(RuntimeError):
    """Report a safe client close reason plus the internal relay failure."""

    def __init__(self, message: str, *, close_code: int = 1011, reason: str) -> None:
        super().__init__(message)
        self.close_code = close_code
        self.reason = reason


@dataclass
class _RelayCounters:
    client_frames: int = 0
    client_bytes: int = 0
    upstream_frames: int = 0
    upstream_bytes: int = 0


def service_websocket_url(
    base_url: str,
    path: str,
    query: str,
) -> str:
    """Build a ws/wss URL beneath an operator-registered HTTP service origin."""
    parsed = urlparse.urlsplit(base_url)
    scheme = {"http": "ws", "https": "wss", "ws": "ws", "wss": "wss"}.get(
        parsed.scheme.lower()
    )
    if scheme is None or not parsed.hostname:
        raise ValueError("managed service base_url must use http, https, ws, or wss")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("managed service base_url must not contain credentials")
    if parsed.query or parsed.fragment:
        raise ValueError("managed service base_url must not contain a query or fragment")
    encoded_path = urlparse.quote(path, safe="/")
    base_path = parsed.path.rstrip("/")
    target_path = f"{base_path}/{encoded_path}" if encoded_path else base_path or "/"
    return urlparse.urlunsplit((scheme, parsed.netloc, target_path, query, ""))


def proxy_request_headers(websocket: WebSocket) -> list[tuple[str, str]]:
    """Copy application headers while stripping credentials and upgrade machinery."""
    return [
        (key, value)
        for key, value in websocket.headers.items()
        if key.lower() not in _SENSITIVE_HEADERS
        and key.lower() not in _HANDSHAKE_HEADERS
        and key.lower() != "origin"
    ]


def requested_subprotocols(websocket: WebSocket) -> list[str]:
    """Parse a bounded list of valid client-requested WebSocket subprotocol tokens."""
    raw = websocket.headers.get("sec-websocket-protocol", "")
    values = [value.strip() for value in raw.split(",") if value.strip()]
    if len(values) > 16 or any(not _SUBPROTOCOL.fullmatch(value) for value in values):
        raise ServiceWebSocketProxyError(
            "invalid WebSocket subprotocol request",
            close_code=1008,
            reason="invalid subprotocol",
        )
    return list(dict.fromkeys(values))


async def proxy_managed_service_websocket(
    downstream: WebSocket,
    *,
    upstream_url: str,
    config: ServiceWebSocketProxyConfig,
    on_result: Callable[[ServiceWebSocketProxyResult], None] | None = None,
) -> ServiceWebSocketProxyResult:
    """Open one bounded upstream connection and relay frames with backpressure."""
    subprotocols = requested_subprotocols(downstream)
    connect_options: dict[str, Any] = {
        "origin": downstream.headers.get("origin"),
        "subprotocols": subprotocols or None,
        "compression": "deflate",
        "open_timeout": config.open_timeout_seconds,
        "ping_interval": min(20.0, config.idle_timeout_seconds / 2),
        "ping_timeout": min(20.0, config.idle_timeout_seconds / 2),
        "close_timeout": config.close_timeout_seconds,
        "max_size": config.max_message_bytes,
        "max_queue": config.max_queue_messages,
        "write_limit": config.write_limit_bytes,
    }
    header_parameter = (
        "additional_headers"
        if "additional_headers" in inspect.signature(websockets.connect).parameters
        else "extra_headers"
    )
    connect_options[header_parameter] = proxy_request_headers(downstream)
    if "proxy" in inspect.signature(websockets.connect).parameters:
        connect_options["proxy"] = None
    try:
        async with websockets.connect(upstream_url, **connect_options) as upstream:
            await downstream.accept(subprotocol=upstream.subprotocol)
            return await _relay(downstream, upstream, config, on_result)
    except ServiceWebSocketProxyError:
        raise
    except Exception as error:
        await close_websocket(downstream, 1013, "upstream unavailable")
        raise ServiceWebSocketProxyError(
            f"managed service WebSocket upstream failed: {error}",
            close_code=1013,
            reason="upstream unavailable",
        ) from error


async def _relay(
    downstream: WebSocket,
    upstream: Any,
    config: ServiceWebSocketProxyConfig,
    on_result: Callable[[ServiceWebSocketProxyResult], None] | None,
) -> ServiceWebSocketProxyResult:
    counters = _RelayCounters()
    loop = asyncio.get_running_loop()
    activity = [loop.time()]
    recorded = [False]

    def finish(result: ServiceWebSocketProxyResult) -> ServiceWebSocketProxyResult:
        if not recorded[0] and on_result is not None:
            recorded[0] = True
            on_result(result)
        return result

    tasks = {
        asyncio.create_task(
            _client_to_upstream(downstream, upstream, config, counters, activity, finish)
        ),
        asyncio.create_task(
            _upstream_to_client(downstream, upstream, counters, activity, finish)
        ),
        asyncio.create_task(
            _idle_watchdog(downstream, upstream, config, counters, activity, finish)
        ),
    }
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    if pending:
        grace_done, pending = await asyncio.wait(pending, timeout=0.05)
        done.update(grace_done)
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    completed = await asyncio.gather(*done, return_exceptions=True)
    results = [result for result in completed if isinstance(result, ServiceWebSocketProxyResult)]
    if results:
        priority = {
            "message_too_large": 4,
            "idle_timeout": 3,
            "upstream_closed": 2,
            "client_closed": 1,
        }
        return max(results, key=lambda result: priority.get(result.outcome, 0))
    error = next((result for result in completed if isinstance(result, BaseException)), None)
    if error is not None:
        raise error
    raise ServiceWebSocketProxyError(
        "managed service WebSocket relay ended without a result",
        reason="relay failed",
    )


async def _client_to_upstream(
    downstream: WebSocket,
    upstream: Any,
    config: ServiceWebSocketProxyConfig,
    counters: _RelayCounters,
    activity: list[float],
    finish: Callable[[ServiceWebSocketProxyResult], ServiceWebSocketProxyResult],
) -> ServiceWebSocketProxyResult:
    while True:
        message = await downstream.receive()
        if message["type"] == "websocket.disconnect":
            code = _close_code(message.get("code"), default=1000)
            reason = _close_reason(message.get("reason"))
            result = finish(_result("client_closed", code, reason, counters))
            await upstream.close(code=code, reason=reason)
            return result
        payload = message.get("text")
        if payload is None:
            payload = message.get("bytes")
        if payload is None:
            continue
        size = _payload_size(payload)
        if size > config.max_message_bytes:
            reason = f"message exceeds {config.max_message_bytes} bytes"
            result = finish(_result("message_too_large", 1009, reason, counters))
            await asyncio.gather(
                close_websocket(downstream, 1009, reason),
                upstream.close(code=1009, reason=reason),
                return_exceptions=True,
            )
            return result
        await upstream.send(payload)
        counters.client_frames += 1
        counters.client_bytes += size
        activity[0] = asyncio.get_running_loop().time()


async def _upstream_to_client(
    downstream: WebSocket,
    upstream: Any,
    counters: _RelayCounters,
    activity: list[float],
    finish: Callable[[ServiceWebSocketProxyResult], ServiceWebSocketProxyResult],
) -> ServiceWebSocketProxyResult:
    try:
        while True:
            payload = await upstream.recv()
            size = _payload_size(payload)
            if isinstance(payload, str):
                await downstream.send_text(payload)
            else:
                await downstream.send_bytes(payload)
            counters.upstream_frames += 1
            counters.upstream_bytes += size
            activity[0] = asyncio.get_running_loop().time()
    except ConnectionClosed as error:
        close_frame = getattr(error, "rcvd", None) or getattr(error, "sent", None)
        code = _close_code(getattr(close_frame, "code", None), default=1011)
        reason = _close_reason(getattr(close_frame, "reason", None))
        result = finish(_result("upstream_closed", code, reason, counters))
        await close_websocket(downstream, code, reason)
        return result


async def _idle_watchdog(
    downstream: WebSocket,
    upstream: Any,
    config: ServiceWebSocketProxyConfig,
    counters: _RelayCounters,
    activity: list[float],
    finish: Callable[[ServiceWebSocketProxyResult], ServiceWebSocketProxyResult],
) -> ServiceWebSocketProxyResult:
    interval = min(1.0, config.idle_timeout_seconds / 2)
    while True:
        await asyncio.sleep(interval)
        if asyncio.get_running_loop().time() - activity[0] < config.idle_timeout_seconds:
            continue
        reason = "managed service WebSocket idle timeout"
        result = finish(_result("idle_timeout", 1001, reason, counters))
        await asyncio.gather(
            close_websocket(downstream, 1001, reason),
            upstream.close(code=1001, reason=reason),
            return_exceptions=True,
        )
        return result


def _payload_size(payload: str | bytes) -> int:
    return len(payload.encode("utf-8")) if isinstance(payload, str) else len(payload)


def _result(
    outcome: str,
    close_code: int,
    reason: str,
    counters: _RelayCounters,
) -> ServiceWebSocketProxyResult:
    return ServiceWebSocketProxyResult(
        outcome=outcome,
        close_code=close_code,
        reason=reason,
        client_frames=counters.client_frames,
        client_bytes=counters.client_bytes,
        upstream_frames=counters.upstream_frames,
        upstream_bytes=counters.upstream_bytes,
    )


async def close_websocket(websocket: WebSocket, code: int, reason: str) -> None:
    """Close a downstream connection without masking its established terminal state."""
    try:
        await websocket.close(code=code, reason=reason[:123])
    except Exception:
        return


def _close_code(value: Any, *, default: int) -> int:
    return value if isinstance(value, int) and value in _VALID_CLOSE_CODES else default


def _close_reason(value: Any) -> str:
    return str(value or "")[:123]


def filtered_query_string(raw_query: str, *, excluded: Iterable[str]) -> str:
    """Remove control-plane selectors and credentials before upstream forwarding."""
    excluded_keys = {key.lower() for key in excluded}
    return urlparse.urlencode(
        [
            (key, value)
            for key, value in urlparse.parse_qsl(raw_query, keep_blank_values=True)
            if key.lower() not in excluded_keys
        ],
        doseq=True,
    )
