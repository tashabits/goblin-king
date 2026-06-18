"""Bring up the Hub stack, run workbook proof, and tear it down."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from urllib import error as urlerror
from urllib import request as urlrequest

from local_image_loader import load_images_for_current_context


def main() -> None:
    """Run the full local Kubernetes JupyterHub workbook proof."""
    args = _parse_args()
    port_forward = None
    tag = f"workbook-proof-{int(time.time())}"
    try:
        images = _prepare_local_images(args.kind_cluster, tag)
        _run_make(args.make, args.stack_config, "jupyterhub-stack-down", check=False)
        _run_make(
            args.make,
            args.stack_config,
            "jupyterhub-stack-up",
            [
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
                )
            ],
        )
        port_forward = subprocess.Popen(
            [
                "kubectl",
                "port-forward",
                f"svc/{args.release}-api",
                f"{args.local_port}:8000",
                "--namespace",
                args.namespace,
            ]
        )
        api_url = f"http://127.0.0.1:{args.local_port}"
        _wait_for_api(f"{api_url}/health")
        _wait_for_authenticated_api(api_url, args.token)
        proof_command = [
            sys.executable,
            args.workbook_proof,
            "--api-url",
            api_url,
            "--token",
            args.token,
            "--project-id",
            args.project_id,
            "--kind",
            args.kind,
        ]
        if args.repository_url:
            proof_command.extend(["--repository-url", args.repository_url])
        subprocess.run(proof_command, check=True)
    finally:
        if port_forward is not None:
            port_forward.terminate()
            try:
                port_forward.wait(timeout=10)
            except subprocess.TimeoutExpired:
                port_forward.kill()
        _run_make(args.make, args.stack_config, "jupyterhub-stack-down", check=False)


def _run_make(
    make_executable: str,
    stack_config: str,
    target: str,
    extra_args: list[str] | None = None,
    *,
    check: bool = True,
) -> None:
    subprocess.run(
        [make_executable, "-f", "Makefile", "-f", stack_config, target, *(extra_args or [])],
        check=check,
    )


def _prepare_local_images(kind_cluster: str, tag: str) -> dict[str, str]:
    """Build local images needed by the proof stack and load them into Kubernetes."""
    images = {
        "app": f"goblin-king:{tag}",
        "admin": f"goblin-king-admin-ui:{tag}",
        "repository_ui": f"goblin-king-repository-ui:{tag}",
        "notebook_runner": f"goblin-king-notebook-python-function:{tag}",
        "notebook_service_runner": f"goblin-king-notebook-asgi-service:{tag}",
        "long_hello": f"goblin-king-example-long-hello:{tag}",
    }
    contexts = {
        images["app"]: (".", None),
        images["admin"]: ("admin-ui", None),
        images["repository_ui"]: (".", "repository-ui/Dockerfile"),
        images["notebook_runner"]: ("workers/notebook.python-function", None),
        images["notebook_service_runner"]: ("workers/notebook.asgi-service", None),
        images["long_hello"]: ("workers/example.long-hello", None),
    }
    for image, (context, dockerfile) in contexts.items():
        command = ["docker", "build", "-t", image]
        if dockerfile:
            command.extend(["-f", dockerfile])
        command.append(context)
        subprocess.run(command, check=True)
    load_images_for_current_context(list(contexts), kind_cluster)
    return images


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


def _wait_for_authenticated_api(api_url: str, token: str) -> None:
    """Wait until the API can validate the Hub-backed workbook token."""
    deadline = time.monotonic() + 180
    request = urlrequest.Request(
        f"{api_url}/goblins",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    last_error = "auth readiness timed out"
    while time.monotonic() < deadline:
        try:
            with urlrequest.urlopen(request, timeout=10) as response:
                if response.status == 200:
                    return
                last_error = f"unexpected status {response.status}"
        except urlerror.HTTPError as error:
            body = error.read().decode("utf-8", errors="replace")
            last_error = f"{error.code}: {body}"
            if error.code not in {503, 504}:
                raise RuntimeError(f"authenticated API check failed: {last_error}") from error
        except OSError as error:
            last_error = str(error)
        time.sleep(3)
    raise TimeoutError(f"authenticated API did not become reachable: {last_error}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--make", default="make")
    parser.add_argument("--stack-config", required=True)
    parser.add_argument("--namespace", default="default")
    parser.add_argument("--release", default="goblin-king")
    parser.add_argument("--local-port", default="18000")
    parser.add_argument("--token", required=True)
    parser.add_argument("--project-id", default="default")
    parser.add_argument("--kind", default="notebook.workbook-short-hello")
    parser.add_argument("--kind-cluster", default="kind")
    parser.add_argument(
        "--repository-url",
        default=os.environ.get("GOBLIN_KING_REPOSITORY_URL", ""),
    )
    parser.add_argument(
        "--workbook-proof",
        default="examples/jupyterhub-goblin-king/workbook_proof.py",
    )
    return parser.parse_args()


if __name__ == "__main__":
    main()
