"""Docker-mode proof for notebook-authored ASGI services."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from urllib import request as urlrequest

from goblin_king.notebooks import GoblinKingNotebookClient

SERVICE_SOURCE = """
from fastapi import FastAPI

app = FastAPI()

@app.get("/hello")
def hello():
    return {
        "message": "Hello World",
        "source": "docker-mode-notebook-service",
    }
""".strip()


def main() -> None:
    args = _parse_args()
    _wait_for_api(f"{args.api_url.rstrip('/')}/health")
    client = GoblinKingNotebookClient(
        api_url=args.api_url,
        token=args.token,
        repository_url=args.repository_url,
    )
    service = None
    try:
        _stop_if_present(client, args.kind)
        service = client.declare_service(
            source=SERVICE_SOURCE,
            kind=args.kind,
            display_name="Docker Notebook Long Hello",
            app_name="app",
            requirements=["fastapi>=0.115,<1"],
            probe_path="/hello",
            project_id=args.project_id,
        )
        validation = service.validate(timeout_seconds=args.timeout_seconds)
        started = service.start(timeout_seconds=args.timeout_seconds, progress=True)
        probe = service.probe()
        proxied = service.proxy("/hello")
        stopped = service.stop()
        _assert_no_service_containers(args.kind)
        print(
            json.dumps(
                {
                    "ok": True,
                    "validation": validation["runtime"],
                    "service_id": started["service"]["id"],
                    "runtime": started["runtime"],
                    "probe": probe["response"].get("json"),
                    "proxy": proxied,
                    "stopped": stopped["notebook_service"]["runtime_status"],
                    "repository_url": client.repository_url,
                },
                indent=2,
            )
        )
    except Exception:
        if service is not None:
            try:
                service.stop()
            except Exception:
                pass
        raise


def _wait_for_api(url: str) -> None:
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        try:
            with urlrequest.urlopen(url, timeout=5) as response:
                if response.status == 200:
                    return
        except Exception:
            time.sleep(2)
    raise TimeoutError(f"API did not become reachable: {url}")


def _stop_if_present(client: GoblinKingNotebookClient, kind: str) -> None:
    try:
        client.stop_service(kind)
    except RuntimeError as error:
        if "404" not in str(error):
            raise


def _assert_no_service_containers(kind: str) -> None:
    completed = subprocess.run(
        [
            "docker",
            "ps",
            "-a",
            "--filter",
            f"label=goblin-king.notebook-service-kind={kind}",
            "--format",
            "{{.Names}}",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    names = [line for line in completed.stdout.splitlines() if line.strip()]
    if names:
        raise RuntimeError(f"notebook service containers still exist: {names}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-url", default="http://127.0.0.1:8000")
    parser.add_argument("--token", default="local-dev-token")
    parser.add_argument("--project-id", default="default")
    parser.add_argument("--kind", default="notebook.docker-long-hello")
    parser.add_argument(
        "--repository-url",
        default=os.environ.get("GOBLIN_KING_REPOSITORY_URL", ""),
    )
    parser.add_argument("--timeout-seconds", type=int, default=180)
    return parser.parse_args()


if __name__ == "__main__":
    main()
