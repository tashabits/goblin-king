"""Small persistent HTTP service used by the delivery acceptance suite."""

import base64
import hashlib
import json
import os
import struct
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import ProxyHandler, build_opener
from uuid import uuid4


def outbound(activity="service"):
    proxy = os.environ.get("HTTP_PROXY")
    if not proxy:
        return {"configured": False}
    try:
        client = build_opener(ProxyHandler({"http": proxy}))
        with client.open(
            "http://allowed.fixture.example.test:8080/work/" + revision() + "/" + activity,
            timeout=5,
        ) as response:
            return {"status": response.status, "body": json.load(response)}
    except Exception as error:
        return {"error": type(error).__name__}


def record_work():
    root = Path(os.environ.get("DATA_DIR", "/data"))
    root.mkdir(parents=True, exist_ok=True)
    with (root / "activity.jsonl").open("a") as stream:
        stream.write(
            json.dumps(
                {
                    "time": time.time(),
                    "revision": revision(),
                    "outbound": outbound(
                        "background" if "--background" in sys.argv else "schedule"
                    ),
                }
            )
            + "\n"
        )


def revision():
    return os.environ.get("SOURCE_REVISION", "development")


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/ws" and self.headers.get("Upgrade", "").lower() == "websocket":
            self.send_response(101)
            self.send_header("Upgrade", "websocket")
            self.send_header("Connection", "Upgrade")
            value = self.headers["Sec-WebSocket-Key"] + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
            self.send_header(
                "Sec-WebSocket-Accept",
                base64.b64encode(hashlib.sha1(value.encode()).digest()).decode(),
            )
            self.end_headers()
            self.connection.settimeout(60)
            try:
                while True:
                    header = self.rfile.read(2)
                    if len(header) != 2 or header[0] & 15 == 8:
                        break
                    length = header[1] & 127
                    if length == 126:
                        length = struct.unpack("!H", self.rfile.read(2))[0]
                    if length > 4096 or length == 127 or not header[1] & 128:
                        break
                    mask = self.rfile.read(4)
                    body = self.rfile.read(length)
                    if len(mask) != 4 or len(body) != length:
                        break
                    body = bytes(value ^ mask[index % 4] for index, value in enumerate(body))
                    self.wfile.write(
                        bytes([129, len(body)]) + body
                        if len(body) < 126
                        else b"\x81\x7e" + struct.pack("!H", len(body)) + body
                    )
                    self.wfile.flush()
            except OSError:
                pass
            self.close_connection = True
            return
        if self.path == "/slow":
            time.sleep(5)
        if self.path == "/outbound":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps(outbound()).encode())
            return
        root = Path(os.environ.get("DATA_DIR", "/data"))
        root.mkdir(parents=True, exist_ok=True)
        sentinel = root / "sentinel"
        if not sentinel.exists():
            sentinel.write_text(str(uuid4()))
        healthy = os.environ.get("FAIL_HEALTH") != "1"
        self.send_response(200 if healthy else 503)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(
            json.dumps(
                {
                    "healthy": healthy,
                    "implementation": "python",
                    "revision": revision(),
                    "sentinel": sentinel.read_text(),
                    "forwarded_for": self.headers.get("X-Forwarded-For"),
                    "forwarded_proto": self.headers.get("X-Forwarded-Proto"),
                }
            ).encode()
        )

    def do_POST(self):
        size = int(self.headers.get("Content-Length", "0"))
        if size > 10485760:
            self.send_error(413)
            return
        body = self.rfile.read(size)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(json.dumps({"bytes": len(body)}).encode())


if __name__ == "__main__":
    if "--work-once" in sys.argv:
        record_work()
    elif "--background" in sys.argv:
        while True:
            record_work()
            time.sleep(2)
    else:
        ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
