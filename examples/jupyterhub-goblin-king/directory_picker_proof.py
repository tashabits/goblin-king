"""Prove the JupyterLab Goblin Directory picker user-server path."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from urllib import error as urlerror
from urllib import request as urlrequest

from directory_ui_proof import (
    _approve_and_publish,
    _expect_forbidden_directory_ui,
    _function_bundle,
    _hub_login,
    _open_directory_ui,
    _service_bundle,
    _submit_bundle,
    _validate_and_request_review,
    _wait_for_url,
)
from full_stack_directory_proof import (
    _assert_no_docker_notebook_service_containers,
    _assert_no_kubernetes_resources,
)
from full_stack_workbook_proof import _prepare_local_images, _run_make
from seed_user_workbooks import _start_user_server, _wait_for_user_pod


def main() -> None:
    """Run the local Kubernetes proof for the JupyterLab Directory picker."""
    args = _parse_args()
    port_forward = None
    tag = f"directory-picker-proof-{int(time.time())}"
    try:
        images = _prepare_local_images(
            args.kind_cluster,
            tag,
            include_singleuser=True,
        )
        _run_make(args.make, args.stack_config, "jupyterhub-stack-down", check=False)
        _run_make(
            args.make,
            args.stack_config,
            "jupyterhub-stack-up",
            [
                "GOBLIN_REPOSITORY_ENABLED=1",
                "GOBLIN_DIRECTORY_UI_ENABLED=1",
                "GOBLIN_DIRECTORY_PICKER_ENABLED=1",
                f"JUPYTERHUB_STACK_IMAGE_TAG={tag}",
                f"JUPYTERHUB_WORKBOOK_USER_TOKEN={args.alice_token}",
                f"JUPYTERHUB_WORKBOOK_ALICE_TOKEN={args.alice_token}",
                f"JUPYTERHUB_WORKBOOK_BOB_TOKEN={args.bob_token}",
                f"JUPYTERHUB_WORKBOOK_CAROL_TOKEN={args.carol_token}",
                f"JUPYTERHUB_WORKBOOK_MALLORY_TOKEN={args.mallory_token}",
                (
                    "HELM_ARGS=-f examples/jupyterhub-goblin-king/goblin-king.values.yaml "
                    f"--set image.tag={tag} "
                    "--set image.pullPolicy=Never "
                    f"--set admin.image.tag={tag} "
                    "--set admin.image.pullPolicy=Never "
                    f"--set workers.exampleLongHello.image={images['long_hello']} "
                    "--set workers.exampleLongHello.pullPolicy=Never "
                    f"--set config.notebookFunctionImage={images['notebook_runner']} "
                    f"--set config.notebookServiceImage={images['notebook_service_runner']}"
                ),
            ],
        )
        port_forward = subprocess.Popen(
            [
                "kubectl",
                "port-forward",
                "svc/proxy-public",
                f"{args.proxy_port}:http",
                "--namespace",
                args.namespace,
            ]
        )
        proxy_url = f"http://127.0.0.1:{args.proxy_port}"
        _wait_for_url(f"{proxy_url}/hub/health", timeout_seconds=180)

        bob = _hub_login(proxy_url, "bob", args.password)
        alice = _hub_login(proxy_url, "alice", args.password)
        carol = _hub_login(proxy_url, "carol", args.password)
        mallory = _hub_login(proxy_url, "mallory", args.password)
        _open_directory_ui(proxy_url, bob)
        _open_directory_ui(proxy_url, alice)
        _open_directory_ui(proxy_url, carol)
        _expect_forbidden_directory_ui(proxy_url, mallory)

        function_entry = _submit_bundle(
            proxy_url,
            bob,
            _function_bundle(args.function_name),
        )["entry"]
        service_entry = _submit_bundle(
            proxy_url,
            bob,
            _service_bundle(args.service_name),
        )["entry"]
        _validate_and_request_review(proxy_url, bob, function_entry["id"])
        _validate_and_request_review(proxy_url, bob, service_entry["id"])
        _approve_and_publish(proxy_url, alice, function_entry["id"])
        _approve_and_publish(proxy_url, alice, service_entry["id"])

        _start_user_server(proxy_url, "carol", args.carol_token, args.timeout_seconds)
        carol_pod = _wait_for_user_pod(args.namespace, "carol", args.timeout_seconds)
        _assert_picker_installed(args.namespace, carol_pod)
        _wait_for_user_url(carol, f"{proxy_url}/user/carol/lab", timeout_seconds=180)

        entries = _user_server_json(
            proxy_url,
            carol,
            "GET",
            "/user/carol/goblin-directory/api/entries?status=published&limit=100",
        )
        names = {item["entry"]["name"] for item in entries["items"]}
        if args.function_name not in names or args.service_name not in names:
            raise RuntimeError(f"published entries were not visible in JupyterLab: {names}")

        run = _user_server_json(
            proxy_url,
            carol,
            "POST",
            f"/user/carol/goblin-directory/api/functions/{args.function_name}/run",
            {"input": {"name": "JupyterLab Directory Picker"}},
        )
        start = _user_server_json(
            proxy_url,
            carol,
            "POST",
            f"/user/carol/goblin-directory/api/services/{args.service_name}/start",
            {},
        )
        probe = _user_server_json(
            proxy_url,
            carol,
            "POST",
            f"/user/carol/goblin-directory/api/services/{args.service_name}/probe",
            {},
        )
        proxied = _user_server_json(
            proxy_url,
            carol,
            "GET",
            f"/user/carol/goblin-directory/api/services/{args.service_name}/proxy/hello",
        )
        stop = _user_server_json(
            proxy_url,
            carol,
            "POST",
            f"/user/carol/goblin-directory/api/services/{args.service_name}/stop",
            {},
        )
        _expect_mallory_denied(proxy_url, mallory, args)
        _assert_no_kubernetes_resources(
            args.namespace,
            "goblin-king.io/notebook-service=true",
            "notebook service runtime resources",
        )
        _assert_no_docker_notebook_service_containers()
        print(
            json.dumps(
                {
                    "ok": True,
                    "carol_pod": carol_pod,
                    "function_entry": function_entry["id"],
                    "service_entry": service_entry["id"],
                    "job_id": run["job"]["id"],
                    "service_id": start["service"]["id"],
                    "probe": probe["probe"],
                    "proxied": proxied,
                    "stop": stop["runtime"],
                },
                indent=2,
            )
        )
    finally:
        if port_forward is not None:
            port_forward.terminate()
            try:
                port_forward.wait(timeout=10)
            except subprocess.TimeoutExpired:
                port_forward.kill()
        _run_make(args.make, args.stack_config, "jupyterhub-stack-down", check=False)
        _assert_no_kubernetes_resources(
            args.namespace,
            "app.kubernetes.io/name=goblin-king",
            "Goblin King stack resources",
        )
        _assert_no_kubernetes_resources(
            args.namespace,
            f"app.kubernetes.io/instance={args.jupyterhub_release}",
            "JupyterHub stack resources",
        )
        _assert_no_kubernetes_resources(
            args.namespace,
            "goblin-king.io/notebook-service=true",
            "notebook service runtime resources",
        )
        _assert_no_docker_notebook_service_containers()


def _user_server_json(
    proxy_url: str,
    opener: urlrequest.OpenerDirector,
    method: str,
    path: str,
    payload: dict[str, object] | None = None,
) -> dict[str, object]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
        headers.update(_xsrf_headers(opener, path))
    request = urlrequest.Request(
        f"{proxy_url}{path}",
        data=data,
        headers=headers,
        method=method,
    )
    try:
        with opener.open(request, timeout=240) as response:
            text = response.read().decode("utf-8", errors="replace")
    except urlerror.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {path} failed with {error.code}: {detail}") from error
    return json.loads(text) if text else {}


def _xsrf_headers(opener: urlrequest.OpenerDirector, request_path: str) -> dict[str, str]:
    jar = getattr(opener, "_gk_cookiejar", None)
    if jar is None:
        return {}
    matches = [
        cookie
        for cookie in jar
        if cookie.name == "_xsrf" and request_path.startswith(cookie.path)
    ]
    if matches:
        cookie = max(matches, key=lambda item: len(item.path))
        return {"X-XSRFToken": cookie.value}
    for cookie in jar:
        if cookie.name == "_xsrf":
            return {"X-XSRFToken": cookie.value}
    return {}


def _wait_for_user_url(
    opener: urlrequest.OpenerDirector,
    url: str,
    *,
    timeout_seconds: float,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error = "not attempted"
    while time.monotonic() < deadline:
        try:
            with opener.open(url, timeout=10) as response:
                if 200 <= response.status < 500:
                    return
                last_error = f"status {response.status}"
        except OSError as error:
            last_error = str(error)
        time.sleep(2)
    raise TimeoutError(f"JupyterLab did not become reachable at {url}: {last_error}")


def _assert_picker_installed(namespace: str, pod: str) -> None:
    server_extensions = _kubectl_exec(
        namespace,
        pod,
        ["jupyter", "server", "extension", "list"],
    )
    if "goblin_king.jupyter_directory" not in server_extensions:
        raise RuntimeError(f"Directory server extension is not enabled: {server_extensions}")
    lab_extensions = _kubectl_exec(namespace, pod, ["jupyter", "labextension", "list"])
    if "goblin-king-jupyterlab" not in lab_extensions:
        raise RuntimeError(f"JupyterLab Directory picker is not installed: {lab_extensions}")


def _kubectl_exec(namespace: str, pod: str, command: list[str]) -> str:
    completed = subprocess.run(
        [
            "kubectl",
            "exec",
            "--namespace",
            namespace,
            pod,
            "--",
            *command,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout + completed.stderr


def _expect_mallory_denied(
    proxy_url: str,
    opener: urlrequest.OpenerDirector,
    args: argparse.Namespace,
) -> None:
    _start_user_server(proxy_url, "mallory", args.mallory_token, args.timeout_seconds)
    _wait_for_user_pod(args.namespace, "mallory", args.timeout_seconds)
    try:
        _user_server_json(
            proxy_url,
            opener,
            "GET",
            "/user/mallory/goblin-directory/api/entries?status=published&limit=100",
        )
    except RuntimeError as error:
        if "403" in str(error) or "401" in str(error):
            return
        raise
    raise RuntimeError("unauthorized user could discover Directory entries through JupyterLab")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--make", default="make")
    parser.add_argument("--stack-config", required=True)
    parser.add_argument("--namespace", default="default")
    parser.add_argument("--release", default="goblin-king")
    parser.add_argument("--jupyterhub-release", default="jupyterhub")
    parser.add_argument("--proxy-port", default="18082")
    parser.add_argument("--kind-cluster", default="kind")
    parser.add_argument("--password", default="goblin")
    parser.add_argument("--alice-token", required=True)
    parser.add_argument("--bob-token", required=True)
    parser.add_argument("--carol-token", required=True)
    parser.add_argument("--mallory-token", required=True)
    parser.add_argument("--timeout-seconds", type=float, default=240)
    parser.add_argument("--function-name", default="picker-proof.hello-function")
    parser.add_argument("--service-name", default="picker-proof.hello-service")
    return parser.parse_args()


if __name__ == "__main__":
    sys.exit(main())
