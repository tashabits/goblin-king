"""Run the managed-service WebSocket echo endpoint or its API proof client."""

from __future__ import annotations

import argparse
import inspect
import json
import time
from typing import Any
from urllib import error as urlerror
from urllib import request as urlrequest

from fastapi import FastAPI, WebSocket
from websockets.exceptions import WebSocketException
from websockets.sync.client import connect

app = FastAPI(title="Managed service WebSocket proof")


@app.get("/health")
def health() -> dict[str, bool]:
    """Expose the readiness endpoint used by the managed-service record."""
    return {"ready": True}


@app.websocket("/{path:path}")
async def echo(websocket: WebSocket, path: str) -> None:
    """Report forwarded metadata, then echo bounded text and binary messages."""
    offered = websocket.headers.get("sec-websocket-protocol", "")
    subprotocol = "l2l.v1" if "l2l.v1" in offered.split(",") else None
    await websocket.accept(subprotocol=subprotocol)
    await websocket.send_json(
        {
            "path": path,
            "query": dict(websocket.query_params),
            "has_authorization": "authorization" in websocket.headers,
            "has_cookie": "cookie" in websocket.headers,
            "has_api_key": "x-api-key" in websocket.headers,
            "proof_header": websocket.headers.get("x-proof"),
        }
    )
    while True:
        message = await websocket.receive()
        if message["type"] == "websocket.disconnect":
            return
        if message.get("text") is not None:
            await websocket.send_text(message["text"])
        elif message.get("bytes") is not None:
            await websocket.send_bytes(message["bytes"])


def main() -> None:
    """Exercise auth, readiness, credential stripping, and duplex relay behavior."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-url", required=True)
    parser.add_argument("--upstream-url", required=True)
    parser.add_argument("--token", default="local-dev-token")
    args = parser.parse_args()
    api_url = args.api_url.rstrip("/")
    _wait_for_health(api_url)
    service = _json_request(
        f"{api_url}/services/long-running",
        method="POST",
        token=args.token,
        payload={
            "kind": "example.long-hello",
            "base_url": args.upstream_url,
            "probe_path": "/health",
        },
    )
    probe = _json_request(
        f"{api_url}/services/long-running/{service['id']}/probe",
        method="POST",
        token=args.token,
    )
    websocket_url = _websocket_url(
        f"{api_url}/services/long-running/{service['id']}/proxy/socket"
    )
    unauthorized_rejected = _unauthorized_rejected(websocket_url)
    headers = {
        "Cookie": "proof-session=must-not-leak",
        "X-Api-Key": "must-not-leak",
        "X-Proof": "compose-or-kubernetes",
    }
    header_name = (
        "additional_headers"
        if "additional_headers" in inspect.signature(connect).parameters
        else "extra_headers"
    )
    options: dict[str, Any] = {
        header_name: headers,
        "subprotocols": ["l2l.v1"],
        "origin": "https://proof.example",
        "open_timeout": 10,
    }
    with connect(f"{websocket_url}?token={args.token}&room=blue", **options) as websocket:
        metadata = json.loads(websocket.recv())
        websocket.send("hello websocket")
        text_echo = websocket.recv()
        websocket.send(b"\x00\x01\x02")
        binary_echo = websocket.recv()
        selected_subprotocol = websocket.subprotocol

    assert probe["service"]["status"] == "running"
    assert unauthorized_rejected
    assert metadata == {
        "path": "socket",
        "query": {"room": "blue"},
        "has_authorization": False,
        "has_cookie": False,
        "has_api_key": False,
        "proof_header": "compose-or-kubernetes",
    }
    assert selected_subprotocol == "l2l.v1"
    assert text_echo == "hello websocket"
    assert binary_echo == b"\x00\x01\x02"
    print(
        json.dumps(
            {
                "service_id": service["id"],
                "probe_status": probe["service"]["status"],
                "unauthorized_rejected": unauthorized_rejected,
                "credentials_stripped": True,
                "text_echo": text_echo,
                "binary_echo_hex": binary_echo.hex(),
                "subprotocol": selected_subprotocol,
            },
            sort_keys=True,
        )
    )


def _json_request(
    url: str,
    *,
    method: str,
    token: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body = json.dumps(payload).encode() if payload is not None else None
    headers = {"Authorization": f"Bearer {token}"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = urlrequest.Request(url, data=body, headers=headers, method=method)
    with urlrequest.urlopen(request, timeout=10) as response:
        result = json.load(response)
    assert isinstance(result, dict)
    return result


def _wait_for_health(api_url: str) -> None:
    for _ in range(60):
        try:
            with urlrequest.urlopen(f"{api_url}/health", timeout=1) as response:
                if response.status == 200:
                    return
        except (OSError, urlerror.URLError):
            time.sleep(0.5)
    raise RuntimeError("managed-service WebSocket proof API did not become healthy")


def _unauthorized_rejected(websocket_url: str) -> bool:
    try:
        with connect(websocket_url, open_timeout=5):
            return False
    except WebSocketException as error:
        response = getattr(error, "response", None)
        status_code = getattr(response, "status_code", None)
        if status_code is None:
            status_code = getattr(error, "status_code", None)
        return status_code in {401, 403}


def _websocket_url(http_url: str) -> str:
    if http_url.startswith("https://"):
        return f"wss://{http_url.removeprefix('https://')}"
    if http_url.startswith("http://"):
        return f"ws://{http_url.removeprefix('http://')}"
    raise ValueError("proof API URL must use http or https")


if __name__ == "__main__":
    main()
