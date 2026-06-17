"""Full-stack proof for JupyterHub-authenticated workbook workflows."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path
from typing import Any
from urllib import error as urlerror
from urllib import request as urlrequest

from local_image_loader import load_images_for_current_context

from goblin_king.notebooks import GoblinKingNotebookClient


def workbook_short_hello(payload):
    """Notebook-defined short goblin used by the full-stack proof."""
    name = payload.get("name", "Workbook")
    return {
        "message": f"Hello {name}",
        "canonical_message": "Hello World",
    }


def main() -> None:
    """Run the same declare/validate/run/proxy flow documented for Hub workbooks."""
    args = _parse_args()
    client = GoblinKingNotebookClient(api_url=args.api_url, token=args.token)
    raw = _RawClient(args.api_url, args.token)
    service_id = None
    try:
        goblin = client.declare(
            workbook_short_hello,
            kind=args.kind,
            display_name="Workbook Short Hello",
            project_id=args.project_id,
            timeout_seconds=30,
        )
        validation = goblin.validate({"name": "Validation"})
        if not validation["validation"]["ok"]:
            raise RuntimeError(f"validation failed: {validation['validation']}")

        short_run = goblin.run({"name": "JupyterHub"}, timeout_seconds=args.timeout_seconds)
        short_result = (short_run.get("run") or {}).get("result") or {}
        if short_result.get("status") != "success":
            raise RuntimeError(f"short goblin failed: {short_run}")

        with tempfile.TemporaryDirectory(prefix="goblin-workbook-service-") as temp_dir:
            service_image = _build_user_created_service_image(Path(temp_dir), args.service_image)
            load_images_for_current_context([service_image], args.kind_cluster)
            service_name = _run_user_created_service(
                image=service_image,
                namespace=args.namespace,
                service_name=args.generated_service_name,
            )
            service_base_url = f"http://{service_name}.{args.namespace}.svc.cluster.local:8080"
            service = raw.request(
                "/services/long-running",
                method="POST",
                payload={
                    "kind": args.generated_service_kind,
                    "image": service_image,
                    "base_url": service_base_url,
                    "probe_path": args.service_probe_path,
                    "project_id": args.project_id,
                },
            )
            service_id = service["id"]
            probe = _request_with_retry(
                raw,
                f"/services/long-running/{service_id}/probe",
                method="POST",
                retry_text="failed with 502",
            )
            proxied = raw.request(
                f"/services/long-running/{service_id}/proxy{args.service_probe_path}"
            )
            stopped = raw.request(f"/services/long-running/{service_id}/stop", method="POST")
            _delete_user_created_service(args.namespace, service_name)
        print(
            json.dumps(
                {
                    "ok": True,
                    "kind": goblin.kind,
                    "validation": validation["validation"],
                    "job_id": short_run["job"]["id"],
                    "run_id": (short_run.get("run") or {}).get("id"),
                    "short_result": short_result,
                    "service_id": service_id,
                    "service_image": service_image,
                    "service_base_url": service_base_url,
                    "service_probe": probe["response"],
                    "service_proxy": proxied,
                    "service_stop": stopped["status"],
                },
                indent=2,
            )
        )
    except Exception:
        if service_id is not None:
            try:
                raw.request(f"/services/long-running/{service_id}/stop", method="POST")
            except Exception:
                pass
        try:
            _delete_user_created_service(args.namespace, args.generated_service_name)
        except Exception:
            pass
        raise


class _RawClient:
    """Minimal standard-library HTTP client for service registration/proxy proof."""

    def __init__(self, api_url: str, token: str) -> None:
        self.api_url = api_url.rstrip("/")
        self.token = token

    def request(
        self,
        path: str,
        *,
        method: str = "GET",
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body = None if payload is None else json.dumps(payload).encode()
        headers = {"Authorization": f"Bearer {self.token}", "Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = urlrequest.Request(
            f"{self.api_url}{path}",
            data=body,
            headers=headers,
            method=method,
        )
        try:
            with urlrequest.urlopen(request, timeout=30) as response:
                raw = response.read().decode("utf-8")
        except urlerror.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"{method} {path} failed with {error.code}: {detail}") from error
        return json.loads(raw) if raw else {}


def _request_with_retry(
    client: _RawClient,
    path: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    retry_text: str,
    timeout_seconds: int = 90,
) -> dict[str, Any]:
    """Retry transient service startup failures from generated workbook services."""
    deadline = time.monotonic() + timeout_seconds
    last_error: RuntimeError | None = None
    while time.monotonic() < deadline:
        try:
            return client.request(path, method=method, payload=payload)
        except RuntimeError as error:
            if retry_text not in str(error):
                raise
            last_error = error
            time.sleep(3)
    assert last_error is not None
    raise last_error


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-url", required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--project-id", default="default")
    parser.add_argument("--kind", default="notebook.workbook-short-hello")
    parser.add_argument("--namespace", default="default")
    parser.add_argument("--kind-cluster", default="kind")
    parser.add_argument("--service-image", default="goblin-king-workbook-hello-service:local")
    parser.add_argument("--generated-service-kind", default="notebook.workbook-long-hello")
    parser.add_argument("--generated-service-name", default="gk-workbook-hello")
    parser.add_argument("--service-probe-path", default="/hello")
    parser.add_argument("--timeout-seconds", type=int, default=180)
    return parser.parse_args()


def _build_user_created_service_image(root: Path, image: str) -> str:
    """Generate a tiny HTTP service from proof-local source and build it."""
    (root / "server.py").write_text(
        textwrap.dedent(
            """
            import json
            from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

            class Handler(BaseHTTPRequestHandler):
                def do_GET(self):
                    if self.path != "/hello":
                        self.send_response(404)
                        self.end_headers()
                        return
                    body = json.dumps({
                        "message": "Hello World",
                        "source": "workbook-created-service",
                    }).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

            ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    (root / "Dockerfile").write_text(
        "FROM python:3.12-slim\n"
        "WORKDIR /service\n"
        "COPY server.py /service/server.py\n"
        'CMD ["python", "/service/server.py"]\n',
        encoding="utf-8",
    )
    subprocess.run(
        ["docker", "build", "-t", image, str(root)],
        check=True,
    )
    return image


def _run_user_created_service(*, image: str, namespace: str, service_name: str) -> str:
    """Create the proof service Deployment and Service from the generated image."""
    manifest = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": service_name, "namespace": namespace},
        "spec": {
            "replicas": 1,
            "selector": {"matchLabels": {"app": service_name}},
            "template": {
                "metadata": {"labels": {"app": service_name}},
                "spec": {
                    "containers": [
                        {
                            "name": "service",
                            "image": image,
                            "imagePullPolicy": "IfNotPresent",
                            "ports": [{"containerPort": 8080}],
                        }
                    ]
                },
            },
        },
    }
    service = {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {"name": service_name, "namespace": namespace},
        "spec": {
            "selector": {"app": service_name},
            "ports": [{"port": 8080, "targetPort": 8080}],
        },
    }
    payload = json.dumps({"apiVersion": "v1", "kind": "List", "items": [manifest, service]})
    subprocess.run(["kubectl", "apply", "-f", "-"], input=payload, text=True, check=True)
    subprocess.run(
        [
            "kubectl",
            "rollout",
            "status",
            f"deployment/{service_name}",
            "--namespace",
            namespace,
            "--timeout=120s",
        ],
        check=True,
    )
    return service_name


def _delete_user_created_service(namespace: str, service_name: str) -> None:
    """Remove the proof-created long service workload from Kubernetes."""
    subprocess.run(
        [
            "kubectl",
            "delete",
            "deployment,service",
            service_name,
            "--namespace",
            namespace,
            "--ignore-not-found",
        ],
        check=False,
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"workbook proof failed: {error}", file=sys.stderr)
        raise
