"""Long-running Hello World service worker for Docker and Kubernetes proof."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse


class LongHelloHandler(BaseHTTPRequestHandler):
    """Serve fresh Hello World probe responses with current timestamps."""

    def do_GET(self) -> None:
        """Return a liveness payload for /, /health, or /hello."""
        parsed = urlparse(self.path)
        if parsed.path not in {"/", "/health", "/hello"}:
            self.send_error(404, "not found")
            return
        payload = {
            "kind": "example.long-hello",
            "message": "Hello World from long running service",
            "timestamp": datetime.now(UTC).isoformat(),
            "worker_id": os.environ.get("GOBLIN_WORKER_ID", "long-hello-service"),
        }
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        """Keep container logs focused on structured application output."""
        return None


def main() -> None:
    """Run the long-lived sample service."""
    port = int(os.environ.get("PORT", "8080"))
    server = ThreadingHTTPServer(("0.0.0.0", port), LongHelloHandler)
    print(json.dumps({"event": "long_hello_started", "port": port}), flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
