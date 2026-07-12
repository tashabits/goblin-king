"""Prove Kubernetes PNG/ZIP retention, download, digest, Job cleanup, and pruning."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

TERMINAL_JOB_STATUSES = {"completed", "failed", "timed_out", "cancelled"}
EXPECTED_ARTIFACTS = {"artifact-proof.png", "artifact-proof.zip"}


def main() -> None:
    """Run the complete live Kubernetes artifact-retention acceptance proof."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-url", default="http://127.0.0.1:18000")
    parser.add_argument("--token", default="local-dev-token")
    parser.add_argument("--namespace", default="goblin-artifact-proof")
    parser.add_argument("--timeout-seconds", type=int, default=180)
    args = parser.parse_args()
    deadline = time.monotonic() + args.timeout_seconds

    job = api_json(
        args.api_url,
        args.token,
        "POST",
        "/jobs",
        {"kind": "example.artifact", "input": {"proof_bundle": True}},
    )
    job = wait_for_job(args.api_url, args.token, job["id"], deadline)
    if job["status"] != "completed":
        raise RuntimeError(f"artifact proof job ended as {job['status']}: {job.get('last_error')}")
    run = wait_for_run(args.api_url, args.token, job["id"], deadline)
    artifacts = api_json(
        args.api_url,
        args.token,
        "GET",
        f"/runs/{run['id']}/artifacts",
    )
    names = {artifact["name"] for artifact in artifacts}
    if names != EXPECTED_ARTIFACTS:
        raise RuntimeError(f"unexpected retained artifacts: {sorted(names)}")

    digests: dict[str, str] = {}
    for name in sorted(names):
        content = api_bytes(
            args.api_url,
            args.token,
            f"/runs/{run['id']}/artifacts/{urllib.parse.quote(name)}",
        )
        digest = hashlib.sha256(content).hexdigest()
        declared = run["result"]["metrics"].get(f"artifact.{name}.sha256")
        if digest != declared:
            raise RuntimeError(f"download digest mismatch for {name}")
        digests[name] = digest

    wait_for_job_cleanup(args.namespace, run["id"], deadline)
    cleanup = api_json(
        args.api_url,
        args.token,
        "POST",
        "/admin/artifacts/cleanup",
        {"dry_run": False, "max_total_bytes": 0},
    )
    if cleanup["files_selected"] < len(EXPECTED_ARTIFACTS):
        raise RuntimeError("artifact cleanup did not select both retained proof files")
    for name in EXPECTED_ARTIFACTS:
        assert_download_missing(args.api_url, args.token, run["id"], name)

    print(
        json.dumps(
            {
                "status": "passed",
                "job_id": job["id"],
                "run_id": run["id"],
                "job_cleanup": "proved",
                "digests": digests,
                "retention_cleanup": cleanup,
            },
            indent=2,
            sort_keys=True,
        )
    )


def wait_for_job(api_url: str, token: str, job_id: str, deadline: float) -> dict[str, Any]:
    """Poll one job until it reaches a terminal state."""
    while time.monotonic() < deadline:
        job = api_json(api_url, token, "GET", f"/jobs/{job_id}")
        if job["status"] in TERMINAL_JOB_STATUSES:
            return job
        time.sleep(1)
    raise TimeoutError(f"job did not finish before proof timeout: {job_id}")


def wait_for_run(api_url: str, token: str, job_id: str, deadline: float) -> dict[str, Any]:
    """Find the persisted Run belonging to the completed proof Job."""
    while time.monotonic() < deadline:
        response = api_json(
            api_url,
            token,
            "GET",
            "/runs?kind=example.artifact&limit=100",
        )
        matching = [run for run in response["items"] if run["job_id"] == job_id]
        if matching:
            return matching[-1]
        time.sleep(0.5)
    raise TimeoutError(f"run was not visible before proof timeout for job: {job_id}")


def wait_for_job_cleanup(namespace: str, run_id: str, deadline: float) -> None:
    """Prove the transient Kubernetes Job has disappeared after bytes were retained."""
    command = [
        "kubectl",
        "get",
        "jobs",
        "--namespace",
        namespace,
        "--selector",
        f"goblin-king.run-id={run_id}",
        "--output",
        "name",
    ]
    while time.monotonic() < deadline:
        completed = subprocess.run(command, check=False, capture_output=True, text=True)
        if completed.returncode != 0:
            raise RuntimeError(f"kubectl Job cleanup check failed: {completed.stderr.strip()}")
        if not completed.stdout.strip():
            return
        time.sleep(1)
    raise TimeoutError(f"transient Kubernetes Job still exists for run: {run_id}")


def assert_download_missing(api_url: str, token: str, run_id: str, name: str) -> None:
    """Require the download endpoint to stop serving bytes after retention cleanup."""
    try:
        api_bytes(
            api_url,
            token,
            f"/runs/{run_id}/artifacts/{urllib.parse.quote(name)}",
        )
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return
        raise
    raise RuntimeError(f"artifact remained downloadable after cleanup: {name}")


def api_json(
    api_url: str,
    token: str,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
) -> Any:
    """Call one authenticated API route and decode its JSON response."""
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        api_url.rstrip("/") + path,
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            **({"Content-Type": "application/json"} if body is not None else {}),
        },
        method=method,
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read())


def api_bytes(api_url: str, token: str, path: str) -> bytes:
    """Download one authenticated artifact response as bytes."""
    request = urllib.request.Request(
        api_url.rstrip("/") + path,
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        return response.read()


if __name__ == "__main__":
    main()
