"""Full-stack proof for JupyterHub-authenticated workbook workflows."""

from __future__ import annotations

import argparse
import json
import sys

from goblin_king.notebooks import GoblinKingNotebookClient

SERVICE_SOURCE = """
from fastapi import FastAPI

app = FastAPI()

@app.get("/hello")
def hello():
    return {
        "message": "Hello World",
        "source": "workbook-defined-asgi-service",
    }
""".strip()


def workbook_short_hello(payload):
    """Notebook-defined short goblin used by the full-stack proof."""
    name = payload.get("name", "Workbook")
    return {
        "message": f"Hello {name}",
        "canonical_message": "Hello World",
    }


def main() -> None:
    """Run the documented declare/validate/run/service flow."""
    args = _parse_args()
    client = GoblinKingNotebookClient(api_url=args.api_url, token=args.token)
    service = None
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

        short_run = goblin.run(
            {"name": "JupyterHub"},
            timeout_seconds=args.timeout_seconds,
            progress=True,
        )
        short_result = (short_run.get("run") or {}).get("result") or {}
        if short_result.get("status") != "success":
            raise RuntimeError(f"short goblin failed: {short_run}")

        _stop_if_present(client, args.generated_service_kind)
        service = client.declare_service(
            source=SERVICE_SOURCE,
            kind=args.generated_service_kind,
            display_name="Workbook Long Hello Service",
            app_name="app",
            requirements=["fastapi>=0.115,<1"],
            probe_path=args.service_probe_path,
            project_id=args.project_id,
        )
        service_validation = service.validate(timeout_seconds=args.timeout_seconds)
        service_start = service.start(timeout_seconds=args.timeout_seconds, progress=True)
        service_probe = service.probe()
        service_proxy = service.proxy(args.service_probe_path)
        service_stop = service.stop()
        print(
            json.dumps(
                {
                    "ok": True,
                    "kind": goblin.kind,
                    "validation": validation["validation"],
                    "job_id": short_run["job"]["id"],
                    "run_id": (short_run.get("run") or {}).get("id"),
                    "short_result": short_result,
                    "service_kind": service.kind,
                    "service_id": service_start["service"]["id"],
                    "service_validation": service_validation,
                    "service_runtime": service_start["runtime"],
                    "service_probe": service_probe["response"],
                    "service_proxy": service_proxy,
                    "service_stop": service_stop["notebook_service"]["runtime_status"],
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


def _stop_if_present(client: GoblinKingNotebookClient, kind: str) -> None:
    try:
        client.stop_service(kind)
    except RuntimeError as error:
        if "404" not in str(error):
            raise


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-url", required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--project-id", default="default")
    parser.add_argument("--kind", default="notebook.workbook-short-hello")
    parser.add_argument("--generated-service-kind", default="notebook.workbook-long-hello")
    parser.add_argument("--service-probe-path", default="/hello")
    parser.add_argument("--timeout-seconds", type=int, default=180)
    return parser.parse_args()


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"workbook proof failed: {error}", file=sys.stderr)
        raise
