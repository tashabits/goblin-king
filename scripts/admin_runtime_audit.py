"""Collect local admin runtime audit evidence from a Goblin King admin API.

This helper is intentionally API-based. Use it alongside browser screenshots from the
React admin console; the browser proves the operator path, and this script produces a
repeatable table of job/run IDs for the PR body.
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

DEFAULT_INPUT = {
    "message": "admin runtime audit",
    "name": "Audit",
    "target": "Audit",
    "value": "Audit",
}
EXPECTED_FAILURE_KINDS = {"example.controlled-failure", "example.behavior-shell-failure"}
LONG_SERVICE_KINDS = {"example.long-hello"}


@dataclass
class AuditRow:
    """One goblin audit result row for a Docker or Helm admin API."""

    kind: str
    job_id: str
    run_id: str
    status: str
    result_status: str
    note: str


def main() -> None:
    """Run the audit and print a markdown table for PR proof."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True, help="Admin base URL, such as http://127.0.0.1:8080")
    parser.add_argument("--token", default="local-dev-token")
    parser.add_argument("--long-service-url", default="http://long-hello:8080")
    parser.add_argument("--poll-seconds", type=int, default=120)
    args = parser.parse_args()

    client = AdminClient(args.base_url.rstrip("/"), args.token, args.long_service_url)
    rows: list[AuditRow] = []
    for goblin in client.get("/admin-api/goblins"):
        kind = goblin["kind"]
        if is_long_service_kind(kind):
            rows.append(client.audit_long_service(kind))
            continue
        rows.append(client.audit_job(kind, timeout_seconds=args.poll_seconds))

    print("| kind | status | result | job/run | notes |")
    print("| --- | --- | --- | --- | --- |")
    for row in rows:
        print(
            f"| `{row.kind}` | `{row.status}` | `{row.result_status}` | "
            f"`{row.job_id}` / `{row.run_id}` | {row.note} |"
        )

    unexpected = [
        row
        for row in rows
        if row.kind not in EXPECTED_FAILURE_KINDS and row.status not in {"completed", "service-ok"}
    ]
    if unexpected:
        raise SystemExit(
            "unexpected audit failures: "
            + ", ".join(f"{row.kind}={row.status}:{row.note}" for row in unexpected)
        )


def is_long_service_kind(kind: str) -> bool:
    """Return true for bundled or project-defined long-running service goblins."""
    return kind in LONG_SERVICE_KINDS or kind.endswith(".long-service")


class AdminClient:
    """Tiny stdlib HTTP client for local Goblin King admin API proof."""

    def __init__(self, base_url: str, token: str, long_service_url: str) -> None:
        self.base_url = base_url
        self.token = token
        self.long_service_url = long_service_url

    def get(self, path: str) -> Any:
        """GET one admin API path."""
        request = urllib.request.Request(
            self.base_url + path,
            headers={"Authorization": f"Bearer {self.token}"},
        )
        return self._open_json(request)

    def post(self, path: str, payload: dict[str, Any] | None = None) -> Any:
        """POST one admin API path with an optional JSON body."""
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {"Authorization": f"Bearer {self.token}"}
        if payload is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            self.base_url + path,
            data=data,
            headers=headers,
            method="POST",
        )
        return self._open_json(request)

    def audit_job(self, kind: str, *, timeout_seconds: int) -> AuditRow:
        """Submit one goblin job and poll until it reaches a terminal state."""
        job = self.post(
            "/admin-api/jobs",
            {"kind": kind, "input": DEFAULT_INPUT, "priority": 100},
        )
        deadline = time.monotonic() + timeout_seconds
        current = job
        while time.monotonic() < deadline:
            current = self.get(f"/admin-api/jobs/{job['id']}")
            if current["status"] in {"completed", "failed", "timed_out", "cancelled"}:
                break
            time.sleep(2)

        runs = self.get("/admin-api/runs?limit=100")
        matching = [run for run in runs["items"] if run["job_id"] == job["id"]]
        run = matching[0] if matching else {}
        result = run.get("result") or {}
        result_status = result.get("status") or "-"
        error = current.get("last_error") or run.get("error") or ""
        note = "expected controlled failure" if kind in EXPECTED_FAILURE_KINDS else error or "ok"
        return AuditRow(
            kind=kind,
            job_id=job["id"],
            run_id=run.get("id", "-"),
            status=current["status"],
            result_status=result_status,
            note=note,
        )

    def audit_long_service(self, kind: str) -> AuditRow:
        """Register and probe the bundled long-running service through the admin API."""
        service = self.post(
            "/admin-api/services/long-running",
            {"kind": kind, "base_url": self.long_service_url},
        )
        first = self.post(f"/admin-api/services/long-running/{service['id']}/probe")
        time.sleep(1)
        second = self.post(f"/admin-api/services/long-running/{service['id']}/probe")
        self.post(f"/admin-api/services/long-running/{service['id']}/stop")
        first_ts = ((first.get("response") or {}).get("json") or {}).get("timestamp")
        second_ts = ((second.get("response") or {}).get("json") or {}).get("timestamp")
        note = (
            "timestamp changed"
            if first_ts and first_ts != second_ts
            else "probe missing timestamp change"
        )
        return AuditRow(
            kind=kind,
            job_id=service["id"],
            run_id="-",
            status="service-ok" if "timestamp changed" in note else "failed",
            result_status="success",
            note=note,
        )

    def _open_json(self, request: urllib.request.Request) -> Any:
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"{request.full_url} failed with {error.code}: {detail}") from error


if __name__ == "__main__":
    main()
