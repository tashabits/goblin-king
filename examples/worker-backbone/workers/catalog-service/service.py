"""Tiny deterministic HTTP service for the portable backbone example."""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

CATALOG = [
    {
        "kind": "example.worker-backbone.normalize-note",
        "workload": "task",
        "description": "Normalize local note text.",
    },
    {
        "kind": "example.worker-backbone.artifact-manifest",
        "workload": "artifact",
        "description": "Write a deterministic manifest artifact.",
    },
    {
        "kind": "example.worker-backbone.local-rag",
        "workload": "rag",
        "description": "Answer from local fixture documents.",
    },
]


class CatalogHandler(BaseHTTPRequestHandler):
    """Serve stable health and catalog payloads."""

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/healthz":
            self._send_json({"status": "ok", "service": "worker-backbone-catalog"})
            return
        if parsed.path == "/v1/items":
            self._send_json({"items": CATALOG, "count": len(CATALOG)})
            return
        self.send_error(404, "not found")

    def log_message(self, format: str, *args: object) -> None:
        """Keep logs quiet for local fixture runs."""
        return None

    def _send_json(self, payload: dict) -> None:
        body = json.dumps(payload, sort_keys=True).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    """Run the local catalog service."""
    port = int(os.environ.get("PORT", "8080"))
    server = ThreadingHTTPServer(("0.0.0.0", port), CatalogHandler)
    print(json.dumps({"event": "catalog_service_started", "port": port}), flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()

