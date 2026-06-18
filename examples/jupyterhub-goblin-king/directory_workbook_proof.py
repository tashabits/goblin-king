"""Workbook-style proof for repository submit, review, and invocation helpers."""

from __future__ import annotations

import argparse
import json
import time
from typing import Any
from urllib import request as urlrequest

from goblin_king.notebooks import GoblinKingNotebookClient

SERVICE_SOURCE = """
from fastapi import FastAPI

app = FastAPI()

@app.get("/hello")
def hello():
    return {
        "message": "Hello World",
        "source": "repository-workbook-proof-service",
    }
""".strip()


def repository_proof_hello(payload):
    """Short workbook-style function submitted to the Directory proof."""
    name = payload.get("name", "Directory")
    return {
        "message": f"Hello {name}",
        "source": "repository-workbook-proof-function",
    }


def main() -> None:
    """Run the submitter, admin, and consumer helper flow."""
    args = _parse_args()
    suffix = str(int(time.time()))
    if args.bob_token and args.alice_token and args.carol_token:
        project = {"id": args.project_id}
        bob_token = args.bob_token
        alice_token = args.alice_token
        carol_token = args.carol_token
        token_source = "jupyterhub"
    else:
        project = _create_project(args.api_url, args.admin_token, f"Directory Proof {suffix}")
        bob_token = _create_user_token(
            args.api_url,
            args.admin_token,
            email=f"bob-{suffix}@example.test",
            display_name=f"Bob {suffix}",
            project_id=project["id"],
            role="member",
        )
        alice_token = _create_user_token(
            args.api_url,
            args.admin_token,
            email=f"alice-{suffix}@example.test",
            display_name=f"Alice {suffix}",
            project_id=project["id"],
            role="admin",
        )
        carol_token = _create_user_token(
            args.api_url,
            args.admin_token,
            email=f"carol-{suffix}@example.test",
            display_name=f"Carol {suffix}",
            project_id=project["id"],
            role="member",
        )
        token_source = "local-api"
    function_name = f"workbook.proof-hello.{suffix}"
    service_name = f"workbook.proof-long-hello.{suffix}"
    repository_url = args.repository_url or None

    bob = GoblinKingNotebookClient(
        api_url=args.api_url,
        repository_url=repository_url,
        token=bob_token,
        request_timeout_seconds=args.timeout_seconds,
    )
    alice = GoblinKingNotebookClient(
        api_url=args.api_url,
        repository_url=repository_url,
        token=alice_token,
        request_timeout_seconds=args.timeout_seconds,
    )
    carol = GoblinKingNotebookClient(
        api_url=args.api_url,
        repository_url=repository_url,
        token=carol_token,
        request_timeout_seconds=args.timeout_seconds,
    )

    service = None
    try:
        function_submission = bob.submit_repository_function(
            repository_proof_hello,
            name=function_name,
            project_id=project["id"],
            display_name="Workbook Proof Hello",
            tags=["workbook", "proof"],
            timeout_seconds=30,
        )
        function_validation = function_submission.validate(
            {"name": "Validation"},
            progress=True,
            timeout_seconds=args.timeout_seconds,
        )
        function_submission.request_review("validated by proof script", progress=True)

        service_submission = bob.submit_repository_service(
            source=SERVICE_SOURCE,
            name=service_name,
            project_id=project["id"],
            display_name="Workbook Proof Long Hello",
            app_name="app",
            requirements=["fastapi>=0.115,<1"],
            probe_path="/hello",
            tags=["workbook", "proof", "service"],
        )
        service_validation = service_submission.validate(
            progress=True,
            timeout_seconds=args.timeout_seconds,
        )
        service_submission.request_review("validated by proof script", progress=True)

        alice.approve_repository_entry(function_submission.entry_id, progress=True)
        function_published = alice.publish_repository_entry(
            function_submission.entry_id,
            progress=True,
        )
        alice.approve_repository_entry(service_submission.entry_id, progress=True)
        service_published = alice.publish_repository_entry(
            service_submission.entry_id,
            progress=True,
        )

        visible = carol.search_repository_entries("workbook.proof", status="published")
        function_run = carol.run_repository_function(
            function_name,
            {"name": "Carol"},
            project_id=project["id"],
            timeout_seconds=args.timeout_seconds,
            progress=True,
        )
        service = carol.repository_service(service_name, project_id=project["id"])
        service_start = service.start(timeout_seconds=args.timeout_seconds, progress=True)
        service_probe = service.probe()
        service_proxy = service.proxy("/hello")
        service_stop = service.stop()
        service = None

        print(
            json.dumps(
                {
                    "ok": True,
                    "token_source": token_source,
                    "project_id": project["id"],
                    "function_name": function_name,
                    "function_entry_id": function_submission.entry_id,
                    "function_validation": function_validation["validation"],
                    "function_published": function_published["entry"],
                    "function_job_id": function_run["job"]["id"],
                    "function_run_id": function_run["run"]["id"],
                    "service_name": service_name,
                    "service_entry_id": service_submission.entry_id,
                    "service_validation": service_validation["validation"],
                    "service_published": service_published["entry"],
                    "service_id": service_start["service"]["id"],
                    "service_probe": service_probe["probe"]["response"].get("json"),
                    "service_proxy": service_proxy,
                    "service_stop": service_stop["notebook_service"]["runtime_status"],
                    "visible_count": visible["meta"]["count"],
                },
                indent=2,
            )
        )
    finally:
        if service is not None:
            try:
                service.stop()
            except Exception:
                pass


def _create_project(api_url: str, admin_token: str, name: str) -> dict[str, Any]:
    return _api_request(
        api_url,
        admin_token,
        "POST",
        "/admin/projects",
        {"name": name},
    )


def _create_user_token(
    api_url: str,
    admin_token: str,
    *,
    email: str,
    display_name: str,
    project_id: str,
    role: str,
) -> str:
    user = _api_request(
        api_url,
        admin_token,
        "POST",
        "/admin/users",
        {"email": email, "display_name": display_name},
    )
    response = _api_request(
        api_url,
        admin_token,
        "POST",
        "/admin/tokens",
        {
            "name": f"{display_name.lower().replace(' ', '-')}-token",
            "user_id": user["id"],
            "project_id": project_id,
            "role": role,
        },
    )
    return str(response["raw_token"])


def _api_request(
    api_url: str,
    token: str,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
    }
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urlrequest.Request(
        f"{api_url.rstrip('/')}{path}",
        data=data,
        headers=headers,
        method=method,
    )
    with urlrequest.urlopen(request, timeout=120) as response:
        body = response.read().decode("utf-8")
    return json.loads(body) if body else {}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-url", required=True)
    parser.add_argument("--admin-token", default="local-dev-token")
    parser.add_argument("--repository-url", default="")
    parser.add_argument("--project-id", default="default")
    parser.add_argument("--bob-token", default="")
    parser.add_argument("--alice-token", default="")
    parser.add_argument("--carol-token", default="")
    parser.add_argument("--timeout-seconds", type=int, default=180)
    return parser.parse_args()


if __name__ == "__main__":
    main()
