"""Notebook helpers for declaring, validating, and running Python function goblins."""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import textwrap
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest

from goblin_king.contracts import GoblinDefinition, NotebookGoblinRecord
from goblin_king.workers import WorkerImageDefinition, WorkerImageMap

NOTEBOOK_WORKER_MODULE = "goblin_king.container_only"
TERMINAL_JOB_STATUSES = {"completed", "failed", "timed_out", "cancelled"}
ProgressCallback = Callable[[dict[str, Any]], None]


def notebook_source_hash(source: str, function_name: str) -> str:
    """Return a stable identity for a notebook-defined function bundle."""
    return hashlib.sha256(f"{function_name}\0{source}".encode()).hexdigest()


def notebook_validation_identity(image_identity: str | None, source_hash: str) -> str:
    """Bind runner image identity and function source identity into one validation key."""
    image_part = image_identity or "<unresolved-runner-image>"
    return hashlib.sha256(f"{image_part}\0{source_hash}".encode()).hexdigest()


def notebook_definition(record: NotebookGoblinRecord) -> GoblinDefinition:
    """Represent a notebook-defined function as a normal container-backed goblin kind."""
    return GoblinDefinition(
        kind=record.kind,
        display_name=record.display_name,
        module=NOTEBOOK_WORKER_MODULE,
        timeout_seconds=record.timeout_seconds,
        max_retries=record.max_retries,
        metadata={
            **record.metadata,
            "workload_type": "notebook-python-function",
            "source_hash": record.source_hash,
            "function_name": record.function_name,
        },
    )


def notebook_worker_map(record: NotebookGoblinRecord) -> WorkerImageMap:
    """Build a one-kind worker map for the notebook Python runner image."""
    return WorkerImageMap.from_definitions(
        {
            record.kind: WorkerImageDefinition(
                context=".",
                image=record.image,
            )
        }
    )


def notebook_worker_input(
    record: NotebookGoblinRecord,
    payload: dict[str, Any] | None,
) -> dict[str, Any]:
    """Wrap user input with the function bundle expected by the notebook runner."""
    return {
        "kind": record.kind,
        "source": record.source,
        "source_hash": record.source_hash,
        "function": record.function_name,
        "payload": payload or {},
    }


@dataclass(frozen=True)
class NotebookFunctionGoblin:
    """Client-side handle returned after a notebook function has been declared."""

    client: GoblinKingNotebookClient
    record: dict[str, Any]

    @property
    def kind(self) -> str:
        """Return the custom goblin kind assigned to this function."""
        return str(self.record["kind"])

    def validate(
        self,
        payload: dict[str, Any] | None = None,
        *,
        require_success: bool = True,
        timeout_seconds: int | None = None,
    ) -> dict[str, Any]:
        """Ask Goblin King to validate this function bundle with the runner image."""
        return self.client.validate(
            self.kind,
            payload or {},
            require_success=require_success,
            timeout_seconds=timeout_seconds,
        )

    def run(
        self,
        payload: dict[str, Any] | None = None,
        *,
        wait: bool = True,
        timeout_seconds: int = 120,
        poll_seconds: float = 1.0,
        progress: bool = False,
        progress_interval_seconds: float = 5.0,
        on_progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        """Submit this function goblin and optionally wait for its run result."""
        return self.client.run(
            self.kind,
            payload or {},
            wait=wait,
            timeout_seconds=timeout_seconds,
            poll_seconds=poll_seconds,
            progress=progress,
            progress_interval_seconds=progress_interval_seconds,
            on_progress=on_progress,
        )


class GoblinKingNotebookClient:
    """Tiny HTTP client intended for JupyterHub workbooks."""

    def __init__(
        self,
        api_url: str | None = None,
        token: str | None = None,
        request_timeout_seconds: float = 120.0,
    ) -> None:
        self.api_url = (api_url or os.environ.get("GOBLIN_KING_API_URL") or "").rstrip("/")
        if not self.api_url:
            self.api_url = "http://127.0.0.1:8000"
        self.request_timeout_seconds = request_timeout_seconds
        self.token = (
            token
            or os.environ.get("GOBLIN_KING_API_TOKEN")
            or os.environ.get("JUPYTERHUB_API_TOKEN")
        )
        if not self.token:
            raise ValueError(
                "token is required; set GOBLIN_KING_API_TOKEN or JUPYTERHUB_API_TOKEN"
            )

    def declare(
        self,
        function: Callable[..., Any],
        *,
        kind: str,
        display_name: str | None = None,
        project_id: str | None = None,
        image: str | None = None,
        timeout_seconds: int | None = None,
        max_retries: int = 0,
        source: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> NotebookFunctionGoblin:
        """Declare a Python function as a buildable notebook goblin bundle."""
        function_name = function.__name__
        record = self._request(
            "POST",
            "/notebooks/goblins",
            {
                "kind": kind,
                "display_name": display_name or kind,
                "project_id": project_id,
                "image": image,
                "source": source or _function_source(function),
                "function_name": function_name,
                "timeout_seconds": timeout_seconds,
                "max_retries": max_retries,
                "metadata": metadata or {},
            },
        )
        return NotebookFunctionGoblin(client=self, record=record)

    def validate(
        self,
        kind: str,
        payload: dict[str, Any],
        *,
        require_success: bool = True,
        timeout_seconds: int | None = None,
    ) -> dict[str, Any]:
        """Validate a declared notebook goblin."""
        return self._request(
            "POST",
            f"/notebooks/goblins/{urlparse.quote(kind, safe='')}/validate",
            {
                "input": payload,
                "require_success": require_success,
                "timeout_seconds": timeout_seconds,
            },
        )

    def run(
        self,
        kind: str,
        payload: dict[str, Any],
        *,
        wait: bool = True,
        timeout_seconds: int = 120,
        poll_seconds: float = 1.0,
        progress: bool = False,
        progress_interval_seconds: float = 5.0,
        on_progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        """Submit a declared notebook goblin and optionally wait for completion."""
        job = self._request("POST", "/jobs", {"kind": kind, "input": payload})
        start = time.monotonic()
        self._emit_progress(
            phase="submitted",
            kind=kind,
            job=job,
            run=None,
            elapsed_seconds=0.0,
            progress=progress,
            on_progress=on_progress,
        )
        if not wait:
            return {"job": job, "run": None}
        deadline = start + timeout_seconds
        next_progress = start + progress_interval_seconds
        latest_job = job
        latest_run = None
        while time.monotonic() < deadline:
            latest_job = self._request("GET", f"/jobs/{job['id']}")
            latest_run = self._run_for_job(job["id"], kind)
            now = time.monotonic()
            job_status = str(latest_job.get("status", ""))
            if job_status in TERMINAL_JOB_STATUSES:
                self._emit_progress(
                    phase=job_status,
                    kind=kind,
                    job=latest_job,
                    run=latest_run,
                    elapsed_seconds=now - start,
                    progress=progress,
                    on_progress=on_progress,
                )
                return {"job": latest_job, "run": latest_run}
            if now >= next_progress:
                self._emit_progress(
                    phase="polling",
                    kind=kind,
                    job=latest_job,
                    run=latest_run,
                    elapsed_seconds=now - start,
                    progress=progress,
                    on_progress=on_progress,
                )
                next_progress = now + progress_interval_seconds
            time.sleep(poll_seconds)
        raise TimeoutError(f"timed out waiting for notebook goblin job {job['id']}")

    def _emit_progress(
        self,
        *,
        phase: str,
        kind: str,
        job: dict[str, Any],
        run: dict[str, Any] | None,
        elapsed_seconds: float,
        progress: bool,
        on_progress: ProgressCallback | None,
    ) -> None:
        """Emit one optional notebook-friendly progress update."""
        if not progress and on_progress is None:
            return
        payload = {
            "phase": phase,
            "kind": kind,
            "job_id": job.get("id"),
            "job_status": job.get("status"),
            "run_id": run.get("id") if run else None,
            "run_status": run.get("status") if run else None,
            "elapsed_seconds": round(elapsed_seconds, 1),
        }
        if on_progress is not None:
            on_progress(payload)
        elif progress:
            run_status = payload["run_status"] or "none"
            print(
                f"[{payload['elapsed_seconds']}s] {kind} "
                f"job={payload['job_status']} run={run_status}"
            )

    def _run_for_job(self, job_id: str, kind: str) -> dict[str, Any] | None:
        runs = self._request(
            "GET",
            f"/runs?kind={urlparse.quote(kind, safe='')}&limit=100",
        )
        for item in runs.get("items", []):
            if item.get("job_id") == job_id:
                return item
        return None

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.token}",
        }
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = urlrequest.Request(
            f"{self.api_url}{path}",
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with urlrequest.urlopen(request, timeout=self.request_timeout_seconds) as response:
                raw = response.read().decode("utf-8")
        except urlerror.HTTPError as error:
            body = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"{method} {path} failed with {error.code}: {body}") from error
        return json.loads(raw) if raw else {}


def _function_source(function: Callable[..., Any]) -> str:
    """Return notebook function source in a form the runner can execute."""
    try:
        return textwrap.dedent(inspect.getsource(function))
    except OSError as error:
        raise ValueError(
            "could not read function source; pass source=... when declaring this goblin"
        ) from error
