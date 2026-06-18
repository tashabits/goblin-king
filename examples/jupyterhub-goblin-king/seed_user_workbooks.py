"""Seed local JupyterHub user servers with example workbooks."""

from __future__ import annotations

import argparse
import json
import shlex
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib import error as urlerror
from urllib import request as urlrequest

DEFAULT_WORKBOOKS = (
    Path("examples/jupyterhub-goblin-king/workbook-launch.ipynb"),
    Path("examples/jupyterhub-goblin-king/workbook-directory-submit.ipynb"),
    Path("examples/jupyterhub-goblin-king/workbook-directory-admin.ipynb"),
    Path("examples/jupyterhub-goblin-king/workbook-directory-consume.ipynb"),
)


def main() -> None:
    """Start expected user servers and copy example workbooks into each one."""
    args = _parse_args()
    users = [_parse_user(value) for value in args.user]
    workbooks = [Path(value) for value in args.workbook] or list(DEFAULT_WORKBOOKS)
    for workbook in workbooks:
        if not workbook.is_file():
            raise FileNotFoundError(f"workbook does not exist: {workbook}")

    port = args.local_port or _free_port()
    port_forward = subprocess.Popen(
        [
            "kubectl",
            "port-forward",
            "--namespace",
            args.namespace,
            f"svc/{args.proxy_service}",
            f"{port}:http",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        proxy_url = f"http://127.0.0.1:{port}"
        _wait_for_url(f"{proxy_url}/hub/health", timeout_seconds=args.timeout_seconds)
        seeded: list[dict[str, Any]] = []
        for user, token in users:
            _start_user_server(proxy_url, user, token, args.timeout_seconds)
            pod = _wait_for_user_pod(args.namespace, user, args.timeout_seconds)
            copied = _copy_workbooks(
                namespace=args.namespace,
                pod=pod,
                destination=args.destination,
                workbooks=workbooks,
            )
            seeded.append(
                {"user": user, "pod": pod, "destination": args.destination, "files": copied}
            )
        print(json.dumps({"ok": True, "seeded": seeded}, indent=2))
    finally:
        port_forward.terminate()
        try:
            port_forward.wait(timeout=10)
        except subprocess.TimeoutExpired:
            port_forward.kill()


def _parse_user(value: str) -> tuple[str, str]:
    """Parse a USER:TOKEN seeding argument."""
    user, separator, token = value.partition(":")
    if not separator or not user or not token:
        raise argparse.ArgumentTypeError("--user must be formatted as USER:TOKEN")
    return user, token


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_url(url: str, *, timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error = "not attempted"
    while time.monotonic() < deadline:
        try:
            with urlrequest.urlopen(url, timeout=5) as response:
                if response.status == 200:
                    return
                last_error = f"status {response.status}"
        except OSError as error:
            last_error = str(error)
        time.sleep(2)
    raise TimeoutError(f"JupyterHub did not become reachable at {url}: {last_error}")


def _start_user_server(
    proxy_url: str,
    user: str,
    token: str,
    timeout_seconds: float,
) -> None:
    request = urlrequest.Request(
        f"{proxy_url}/hub/api/users/{user}/server",
        headers={"Authorization": f"token {token}", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urlrequest.urlopen(request, timeout=timeout_seconds) as response:
            if response.status in {201, 202, 204}:
                return
            raise RuntimeError(f"unexpected Hub server start status for {user}: {response.status}")
    except urlerror.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        if error.code == 400 and "already running" in body.lower():
            return
        raise RuntimeError(
            f"could not start JupyterHub server for {user}: {error.code} {body}"
        ) from error


def _wait_for_user_pod(namespace: str, user: str, timeout_seconds: float) -> str:
    selector = f"hub.jupyter.org/username={user},component=singleuser-server"
    deadline = time.monotonic() + timeout_seconds
    last_state = "not attempted"
    while time.monotonic() < deadline:
        result = subprocess.run(
            [
                "kubectl",
                "get",
                "pods",
                "--namespace",
                namespace,
                "-l",
                selector,
                "-o",
                "json",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        payload = json.loads(result.stdout)
        for item in payload.get("items", []):
            pod_name = item["metadata"]["name"]
            phase = item.get("status", {}).get("phase")
            conditions = item.get("status", {}).get("conditions", [])
            ready = any(
                condition.get("type") == "Ready" and condition.get("status") == "True"
                for condition in conditions
            )
            last_state = f"{pod_name} phase={phase} ready={ready}"
            if phase == "Running" and ready:
                return str(pod_name)
        time.sleep(2)
    raise TimeoutError(f"single-user pod for {user} was not ready: {last_state}")


def _copy_workbooks(
    *,
    namespace: str,
    pod: str,
    destination: str,
    workbooks: list[Path],
) -> list[str]:
    destination_dir = f"/home/jovyan/{destination.strip('/')}"
    subprocess.run(
        [
            "kubectl",
            "exec",
            "--namespace",
            namespace,
            pod,
            "--",
            "sh",
            "-lc",
            f"mkdir -p {shlex.quote(destination_dir)}",
        ],
        check=True,
    )
    copied: list[str] = []
    for workbook in workbooks:
        target_name = _seeded_workbook_name(workbook.name)
        target = f"{destination_dir}/{target_name}"
        subprocess.run(
            [
                "kubectl",
                "exec",
                "-i",
                "--namespace",
                namespace,
                pod,
                "--",
                "sh",
                "-lc",
                f"cat > {shlex.quote(target)}",
            ],
            input=workbook.read_bytes(),
            check=True,
        )
        copied.append(target)
    return copied


def _seeded_workbook_name(name: str) -> str:
    """Return the user-visible workbook name copied into JupyterLab."""
    return name.replace("workbook-directory-", "workbook-directory-")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--namespace", default="default")
    parser.add_argument("--proxy-service", default="proxy-public")
    parser.add_argument("--local-port", type=int, default=0)
    parser.add_argument("--destination", default="examples")
    parser.add_argument("--timeout-seconds", type=float, default=180)
    parser.add_argument("--user", action="append", default=[])
    parser.add_argument("--workbook", action="append", default=[])
    args = parser.parse_args()
    if not args.user:
        print("at least one --user USER:TOKEN is required", file=sys.stderr)
        raise SystemExit(2)
    return args


if __name__ == "__main__":
    main()
