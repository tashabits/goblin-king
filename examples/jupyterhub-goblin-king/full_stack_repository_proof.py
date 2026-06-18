"""Bring up Hub plus repository, run the repository workbook proof, and tear down."""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from urllib import error as urlerror
from urllib import request as urlrequest

from full_stack_workbook_proof import (
    _prepare_local_images,
    _run_make,
    _wait_for_api,
    _wait_for_authenticated_api,
)


def main() -> None:
    """Run the full local Kubernetes JupyterHub repository proof."""
    args = _parse_args()
    port_forward = None
    tag = f"repository-proof-{int(time.time())}"
    try:
        images = _prepare_local_images(args.kind_cluster, tag)
        _run_make(args.make, args.stack_config, "jupyterhub-stack-down", check=False)
        _run_make(
            args.make,
            args.stack_config,
            "jupyterhub-stack-up",
            [
                "GOBLIN_REPOSITORY_ENABLED=1",
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
                f"svc/{args.release}-api",
                f"{args.local_port}:8000",
                "--namespace",
                args.namespace,
            ]
        )
        api_url = f"http://127.0.0.1:{args.local_port}"
        _wait_for_api(f"{api_url}/health")
        _wait_for_authenticated_api(api_url, args.bob_token)
        _wait_for_authenticated_api(api_url, args.alice_token)
        _wait_for_authenticated_api(api_url, args.carol_token)
        _expect_auth_denied(api_url, args.mallory_token)
        subprocess.run(
            [
                sys.executable,
                args.repository_proof,
                "--api-url",
                api_url,
                "--project-id",
                args.project_id,
                "--bob-token",
                args.bob_token,
                "--alice-token",
                args.alice_token,
                "--carol-token",
                args.carol_token,
                "--timeout-seconds",
                str(args.timeout_seconds),
            ],
            check=True,
        )
        _assert_no_kubernetes_resources(
            args.namespace,
            "goblin-king.io/notebook-service=true",
            "notebook service runtime resources",
        )
        _assert_no_docker_notebook_service_containers()
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


def _expect_auth_denied(api_url: str, token: str) -> None:
    """Verify the unauthorized Hub user cannot access API routes."""
    request = urlrequest.Request(
        f"{api_url}/goblins",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    try:
        with urlrequest.urlopen(request, timeout=10) as response:
            raise RuntimeError(f"mallory token was unexpectedly accepted: {response.status}")
    except urlerror.HTTPError as error:
        if error.code not in {401, 403}:
            body = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"unexpected mallory denial response {error.code}: {body}"
            ) from error


def _assert_no_kubernetes_resources(namespace: str, selector: str, description: str) -> None:
    """Fail if selected Kubernetes resources remain after teardown settles."""
    deadline = time.monotonic() + 120
    names = _kubernetes_resource_names(namespace, selector)
    while names and time.monotonic() < deadline:
        time.sleep(2)
        names = _kubernetes_resource_names(namespace, selector)
    if names:
        raise RuntimeError(f"{description} still exist: {names}")


def _kubernetes_resource_names(namespace: str, selector: str) -> list[str]:
    completed = subprocess.run(
        [
            "kubectl",
            "get",
            "deploy,svc,configmap,pod,pvc,secret",
            "--namespace",
            namespace,
            "-l",
            selector,
            "-o",
            "name",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return [line for line in completed.stdout.splitlines() if line.strip()]


def _assert_no_docker_notebook_service_containers() -> None:
    """Fail if Docker-mode notebook service containers remain."""
    completed = subprocess.run(
        [
            "docker",
            "ps",
            "-a",
            "--filter",
            "label=goblin-king.notebook-service-kind",
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
    parser.add_argument("--make", default="make")
    parser.add_argument("--stack-config", required=True)
    parser.add_argument("--namespace", default="default")
    parser.add_argument("--release", default="goblin-king")
    parser.add_argument("--jupyterhub-release", default="jupyterhub")
    parser.add_argument("--local-port", default="18000")
    parser.add_argument("--project-id", default="default")
    parser.add_argument("--alice-token", required=True)
    parser.add_argument("--bob-token", required=True)
    parser.add_argument("--carol-token", required=True)
    parser.add_argument("--mallory-token", required=True)
    parser.add_argument("--kind-cluster", default="kind")
    parser.add_argument("--timeout-seconds", type=int, default=240)
    parser.add_argument(
        "--repository-proof",
        default="examples/jupyterhub-goblin-king/repository_workbook_proof.py",
    )
    return parser.parse_args()


if __name__ == "__main__":
    main()
