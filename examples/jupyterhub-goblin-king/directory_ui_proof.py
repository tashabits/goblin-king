"""Bring up Hub plus directory UI and prove the browser-service workflow."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
import zipfile
from http.cookiejar import CookieJar
from io import BytesIO
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest

from full_stack_directory_proof import (
    _assert_no_docker_notebook_service_containers,
    _assert_no_kubernetes_resources,
)
from full_stack_workbook_proof import _prepare_local_images, _run_make


def main() -> None:
    """Run the local Kubernetes directory UI proof."""
    args = _parse_args()
    port_forward = None
    tag = f"directory-ui-proof-{int(time.time())}"
    try:
        images = _prepare_local_images(args.kind_cluster, tag)
        _run_make(args.make, args.stack_config, "jupyterhub-stack-down", check=False)
        _run_make(
            args.make,
            args.stack_config,
            "jupyterhub-stack-up",
            [
                "GOBLIN_REPOSITORY_ENABLED=1",
                "GOBLIN_DIRECTORY_UI_ENABLED=1",
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

        directory = _ui_json(
            proxy_url,
            carol,
            "GET",
            "/directory/entries?status=published&limit=100",
        )
        names = {item["entry"]["name"] for item in directory["items"]}
        if args.function_name not in names or args.service_name not in names:
            raise RuntimeError(f"published entries were not visible to carol: {names}")

        run = _ui_json(
            proxy_url,
            carol,
            "POST",
            f"/directory/functions/{args.function_name}/run",
            {"input": {"name": "Directory UI Proof"}},
        )
        start = _ui_json(
            proxy_url,
            carol,
            "POST",
            f"/directory/services/{args.service_name}/start",
            {},
        )
        proxy = _ui_json(
            proxy_url,
            carol,
            "GET",
            f"/directory/services/{args.service_name}/proxy/hello",
        )
        stop = _ui_json(
            proxy_url,
            carol,
            "POST",
            f"/directory/services/{args.service_name}/stop",
            {},
        )
        print(
            json.dumps(
                {
                    "ok": True,
                    "function_entry": function_entry["id"],
                    "service_entry": service_entry["id"],
                    "job_id": run["job"]["id"],
                    "service_id": start["service"]["kind"],
                    "proxy": proxy,
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


def _hub_login(proxy_url: str, username: str, password: str) -> urlrequest.OpenerDirector:
    jar = CookieJar()
    opener = urlrequest.build_opener(urlrequest.HTTPCookieProcessor(jar))
    login_url = f"{proxy_url}/hub/login"
    login_page = opener.open(login_url, timeout=30).read().decode("utf-8", errors="replace")
    xsrf = _extract_xsrf(login_page)
    data = urlparse.urlencode(
        {
            "username": username,
            "password": password,
            **({"_xsrf": xsrf} if xsrf else {}),
        }
    ).encode("utf-8")
    request = urlrequest.Request(
        login_url,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    opener.open(request, timeout=30).read()
    return opener


def _open_directory_ui(proxy_url: str, opener: urlrequest.OpenerDirector) -> None:
    try:
        response = opener.open(f"{proxy_url}/services/goblin-directory/", timeout=60)
        body = response.read().decode("utf-8", errors="replace")
    except urlerror.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"directory UI did not open: {error.code} {error.reason}: {detail}"
        ) from error
    if "Goblin Directory" not in body:
        raise RuntimeError(f"directory UI did not render after Hub OAuth: {body[:500]}")


def _expect_forbidden_directory_ui(proxy_url: str, opener: urlrequest.OpenerDirector) -> None:
    try:
        opener.open(f"{proxy_url}/services/goblin-directory/", timeout=60).read()
    except urlerror.HTTPError as error:
        if error.code in {403, 500}:
            return
        raise
    else:
        response = _ui_json(proxy_url, opener, "GET", "/directory/entries?status=published")
        if response.get("items"):
            raise RuntimeError("unauthorized user could access directory UI entries")


def _submit_bundle(
    proxy_url: str,
    opener: urlrequest.OpenerDirector,
    bundle: bytes,
) -> dict[str, object]:
    return _service_json(
        proxy_url,
        opener,
        "POST",
        "/ui-api/bundles/submit",
        data=bundle,
        content_type="application/zip",
    )


def _validate_and_request_review(
    proxy_url: str,
    opener: urlrequest.OpenerDirector,
    entry_id: str,
) -> None:
    _ui_json(
        proxy_url,
        opener,
        "POST",
        f"/directory/entries/{entry_id}/validate",
        {"require_success": True, "timeout_seconds": 180},
    )
    _ui_json(
        proxy_url,
        opener,
        "POST",
        f"/directory/entries/{entry_id}/request-review",
        {},
    )


def _approve_and_publish(
    proxy_url: str,
    opener: urlrequest.OpenerDirector,
    entry_id: str,
) -> None:
    _ui_json(proxy_url, opener, "POST", f"/directory/entries/{entry_id}/approve", {})
    _ui_json(proxy_url, opener, "POST", f"/directory/entries/{entry_id}/publish", {})


def _ui_json(
    proxy_url: str,
    opener: urlrequest.OpenerDirector,
    method: str,
    path: str,
    payload: dict[str, object] | None = None,
) -> dict[str, object]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    return _service_json(
        proxy_url,
        opener,
        method,
        f"/ui-api{path}",
        data=data,
        content_type="application/json" if data is not None else None,
    )


def _service_json(
    proxy_url: str,
    opener: urlrequest.OpenerDirector,
    method: str,
    service_path: str,
    *,
    data: bytes | None = None,
    content_type: str | None = None,
) -> dict[str, object]:
    headers = {"Accept": "application/json"}
    if content_type:
        headers["Content-Type"] = content_type
    request = urlrequest.Request(
        f"{proxy_url}/services/goblin-directory{service_path}",
        data=data,
        headers=headers,
        method=method,
    )
    try:
        with opener.open(request, timeout=240) as response:
            text = response.read().decode("utf-8", errors="replace")
    except urlerror.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {service_path} failed with {error.code}: {detail}") from error
    return json.loads(text) if text else {}


def _function_bundle(name: str) -> bytes:
    return _bundle(
        {
            "goblin-directory.json": json.dumps(
                {
                    "schema_version": 1,
                    "name": name,
                    "type": "notebook_function",
                    "entrypoint": "hello.py",
                    "display_name": "Directory UI Function",
                    "description": "Function submitted through the directory UI proof.",
                    "tags": ["ui-proof", "hello"],
                    "function_name": "run",
                }
            ),
            "hello.py": (
                "def run(payload):\n"
                "    name = payload.get('name', 'Directory')\n"
                "    return {'message': f'Hello {name}', 'source': 'directory-ui-proof'}\n"
            ),
        }
    )


def _service_bundle(name: str) -> bytes:
    return _bundle(
        {
            "goblin-directory.json": json.dumps(
                {
                    "schema_version": 1,
                    "name": name,
                    "type": "notebook_service",
                    "entrypoint": "service.py",
                    "display_name": "Directory UI Service",
                    "description": "ASGI service submitted through the directory UI proof.",
                    "tags": ["ui-proof", "service"],
                    "app_name": "app",
                    "requirements": ["fastapi>=0.115,<1"],
                    "probe_path": "/hello",
                }
            ),
            "service.py": (
                "from fastapi import FastAPI\n"
                "app = FastAPI()\n\n"
                "@app.get('/hello')\n"
                "def hello():\n"
                "    return {\n"
                "        'message': 'Hello from directory UI service',\n"
                "        'source': 'directory-ui-proof',\n"
                "    }\n"
            ),
        }
    )


def _bundle(files: dict[str, str]) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, content in files.items():
            archive.writestr(path, content)
    return buffer.getvalue()


def _extract_xsrf(html: str) -> str | None:
    match = re.search(r'name=["\']_xsrf["\']\s+value=["\']([^"\']+)["\']', html)
    return match.group(1) if match else None


def _wait_for_url(url: str, *, timeout_seconds: int) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error = "timed out"
    while time.monotonic() < deadline:
        try:
            with urlrequest.urlopen(url, timeout=10) as response:
                if 200 <= response.status < 500:
                    return
        except OSError as error:
            last_error = str(error)
        time.sleep(2)
    raise TimeoutError(f"{url} did not become reachable: {last_error}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--make", default="make")
    parser.add_argument("--stack-config", required=True)
    parser.add_argument("--namespace", default="default")
    parser.add_argument("--release", default="goblin-king")
    parser.add_argument("--jupyterhub-release", default="jupyterhub")
    parser.add_argument("--proxy-port", default="18080")
    parser.add_argument("--kind-cluster", default="kind")
    parser.add_argument("--password", default="goblin")
    parser.add_argument("--alice-token", required=True)
    parser.add_argument("--bob-token", required=True)
    parser.add_argument("--carol-token", required=True)
    parser.add_argument("--mallory-token", required=True)
    parser.add_argument("--function-name", default="ui-proof.hello-function")
    parser.add_argument("--service-name", default="ui-proof.hello-service")
    return parser.parse_args()


if __name__ == "__main__":
    sys.exit(main())
