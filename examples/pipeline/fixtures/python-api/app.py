"""Small persistent HTTP service used by the delivery acceptance suite."""

import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


def record_work():
    root = Path(os.environ.get("DATA_DIR", "/data"))
    root.mkdir(parents=True, exist_ok=True)
    with (root / "activity.jsonl").open("a") as stream:
        stream.write(json.dumps({"time": time.time(), "revision": revision()}) + "\n")


def revision():
    return os.environ.get("SOURCE_REVISION", "development")


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        root = Path(os.environ.get("DATA_DIR", "/data"))
        root.mkdir(parents=True, exist_ok=True)
        sentinel = root / "sentinel"
        if not sentinel.exists():
            sentinel.write_text(os.environ.get("SENTINEL", "persistent-python-data"))
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
                }
            ).encode()
        )


if __name__ == "__main__":
    if "--work-once" in sys.argv:
        record_work()
    elif "--background" in sys.argv:
        while True:
            record_work()
            time.sleep(2)
    else:
        HTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
