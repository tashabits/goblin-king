"""Notebook helpers for Python function goblins and ASGI services."""

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


@dataclass
class NotebookASGIService:
    """Client-side handle returned after a notebook ASGI service is declared."""

    client: GoblinKingNotebookClient
    record: dict[str, Any]

    @property
    def kind(self) -> str:
        """Return the custom service kind assigned to this ASGI app."""
        return str(self.record["kind"])

    @property
    def service_id(self) -> str | None:
        """Return the active registered service id, if the service is running."""
        service_id = self.record.get("active_service_id")
        return str(service_id) if service_id else None

    def validate(self, *, timeout_seconds: int = 120) -> dict[str, Any]:
        """Validate this ASGI service by starting and probing an isolated runner."""
        response = self.client.validate_service(self.kind, timeout_seconds=timeout_seconds)
        self.record = response["service"]
        return response

    def start(
        self,
        *,
        timeout_seconds: int = 120,
        progress: bool = False,
        on_progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        """Start this ASGI service and register it for gated proxy access."""
        started_at = time.monotonic()
        self.client._emit_service_progress(
            phase="starting",
            kind=self.kind,
            service=self.record,
            elapsed_seconds=0.0,
            progress=progress,
            on_progress=on_progress,
        )
        try:
            response = self.client.start_service(self.kind, timeout_seconds=timeout_seconds)
        except Exception:
            self.client._emit_service_progress(
                phase="failed",
                kind=self.kind,
                service=self.record,
                elapsed_seconds=time.monotonic() - started_at,
                progress=progress,
                on_progress=on_progress,
            )
            raise
        self.record = response["notebook_service"]
        self.client._emit_service_progress(
            phase="running",
            kind=self.kind,
            service=self.record,
            elapsed_seconds=time.monotonic() - started_at,
            progress=progress,
            on_progress=on_progress,
        )
        return response

    def probe(self) -> dict[str, Any]:
        """Probe the active registered service endpoint."""
        service_id = self._require_service_id()
        return self.client._request("POST", f"/services/long-running/{service_id}/probe")

    def proxy(self, path: str) -> dict[str, Any]:
        """Proxy one request to the active registered service endpoint."""
        service_id = self._require_service_id()
        clean_path = path if path.startswith("/") else f"/{path}"
        return self.client._request(
            "GET",
            f"/services/long-running/{service_id}/proxy"
            f"{urlparse.quote(clean_path, safe='/')}",
        )

    def stop(self) -> dict[str, Any]:
        """Stop and clean up the managed ASGI service runtime."""
        response = self.client.stop_service(self.kind)
        self.record = response["notebook_service"]
        return response

    def _require_service_id(self) -> str:
        service_id = self.service_id
        if not service_id:
            raise RuntimeError("service is not running; call start() first")
        return service_id


@dataclass
class RepositoryASGIService:
    """Client-side handle for an approved repository ASGI service."""

    client: GoblinKingNotebookClient
    name: str
    project_id: str | None = None
    version: int | None = None
    record: dict[str, Any] | None = None

    def start(
        self,
        *,
        timeout_seconds: int = 120,
        progress: bool = False,
        on_progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        """Start this approved repository service by name."""
        started_at = time.monotonic()
        self.client._emit_service_progress(
            phase="starting",
            kind=self.name,
            service=self.record or {},
            elapsed_seconds=0.0,
            progress=progress,
            on_progress=on_progress,
        )
        try:
            response = self.client.start_repository_service(
                self.name,
                project_id=self.project_id,
                version=self.version,
                timeout_seconds=timeout_seconds,
            )
        except Exception:
            self.client._emit_service_progress(
                phase="failed",
                kind=self.name,
                service=self.record or {},
                elapsed_seconds=time.monotonic() - started_at,
                progress=progress,
                on_progress=on_progress,
            )
            raise
        self.record = response["notebook_service"]
        self.client._emit_service_progress(
            phase="running",
            kind=self.name,
            service=self.record,
            elapsed_seconds=time.monotonic() - started_at,
            progress=progress,
            on_progress=on_progress,
        )
        return response

    def probe(self) -> dict[str, Any]:
        """Probe this approved repository service by name."""
        response = self.client.probe_repository_service(
            self.name,
            project_id=self.project_id,
            version=self.version,
        )
        self.record = response["notebook_service"]
        return response

    def proxy(self, path: str) -> dict[str, Any]:
        """Proxy one GET request to this approved repository service by name."""
        return self.client.proxy_repository_service(
            self.name,
            path,
            project_id=self.project_id,
            version=self.version,
        )

    def stop(self) -> dict[str, Any]:
        """Stop this approved repository service by name."""
        response = self.client.stop_repository_service(
            self.name,
            project_id=self.project_id,
            version=self.version,
        )
        self.record = response["notebook_service"]
        return response


@dataclass
class RepositoryEntryHandle:
    """Client-side handle for a submitted repository entry/version."""

    client: GoblinKingNotebookClient
    entry: dict[str, Any]
    version: dict[str, Any]
    notebook: dict[str, Any] | None = None
    versions: list[dict[str, Any]] | None = None
    latest_response: dict[str, Any] | None = None

    @property
    def entry_id(self) -> str:
        """Return the repository entry id."""
        return str(self.entry["id"])

    @property
    def name(self) -> str:
        """Return the project-scoped repository name."""
        return str(self.entry["name"])

    @property
    def type(self) -> str:
        """Return the repository goblin type."""
        return str(self.entry["type"])

    @property
    def project_id(self) -> str | None:
        """Return the project id, when project-scoped."""
        project_id = self.entry.get("project_id")
        return str(project_id) if project_id is not None else None

    @property
    def version_number(self) -> int:
        """Return the submitted source version number."""
        return int(self.version["version"])

    @property
    def kind(self) -> str:
        """Return the immutable runtime kind for this source version."""
        return str(self.version["kind"])

    def refresh(self) -> dict[str, Any]:
        """Reload the entry detail from the repository."""
        response = self.client.get_repository_entry(self.entry_id)
        self._replace_from_response(response)
        return response

    def validate(
        self,
        payload: dict[str, Any] | None = None,
        *,
        require_success: bool = True,
        timeout_seconds: int | None = None,
        progress: bool = False,
        on_progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        """Validate this submitted repository version."""
        response = self.client.validate_repository_entry(
            self.entry_id,
            payload or {},
            require_success=require_success,
            timeout_seconds=timeout_seconds,
            version=self.version_number,
            progress=progress,
            on_progress=on_progress,
        )
        self._replace_from_response(response)
        return response

    def request_review(
        self,
        note: str | None = None,
        *,
        progress: bool = False,
        on_progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        """Request admin review for this validated repository version."""
        response = self.client.request_repository_review(
            self.entry_id,
            note=note,
            version=self.version_number,
            progress=progress,
            on_progress=on_progress,
        )
        self._replace_from_response(response)
        return response

    def approve(
        self,
        note: str | None = None,
        *,
        progress: bool = False,
        on_progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        """Approve this repository version with an admin token."""
        response = self.client.approve_repository_entry(
            self.entry_id,
            note=note,
            version=self.version_number,
            progress=progress,
            on_progress=on_progress,
        )
        self._replace_from_response(response)
        return response

    def publish(
        self,
        *,
        progress: bool = False,
        on_progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        """Publish this approved repository version with an admin token."""
        response = self.client.publish_repository_entry(
            self.entry_id,
            version=self.version_number,
            progress=progress,
            on_progress=on_progress,
        )
        self._replace_from_response(response)
        return response

    def reject(
        self,
        note: str | None = None,
        *,
        progress: bool = False,
        on_progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        """Reject this repository version with an admin token."""
        response = self.client.reject_repository_entry(
            self.entry_id,
            note=note,
            version=self.version_number,
            progress=progress,
            on_progress=on_progress,
        )
        self._replace_from_response(response)
        return response

    def wait_for_status(
        self,
        status: str = "published",
        *,
        timeout_seconds: int = 300,
        poll_seconds: float = 5.0,
        progress: bool = False,
        on_progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        """Wait until the entry reaches a target status."""
        response = self.client.wait_for_repository_status(
            self.entry_id,
            status=status,
            timeout_seconds=timeout_seconds,
            poll_seconds=poll_seconds,
            progress=progress,
            on_progress=on_progress,
        )
        self._replace_from_response(response)
        return response

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
        """Run this repository function after it is published."""
        if self.type != "notebook_function":
            raise RuntimeError("repository entry is not a function goblin")
        return self.client.run_repository_function(
            self.name,
            payload or {},
            project_id=self.project_id,
            version=self.version_number,
            wait=wait,
            timeout_seconds=timeout_seconds,
            poll_seconds=poll_seconds,
            progress=progress,
            progress_interval_seconds=progress_interval_seconds,
            on_progress=on_progress,
        )

    def service(self) -> RepositoryASGIService:
        """Return a service handle for this repository ASGI service."""
        if self.type != "notebook_service":
            raise RuntimeError("repository entry is not an ASGI service goblin")
        return self.client.repository_service(
            self.name,
            project_id=self.project_id,
            version=self.version_number,
        )

    def _replace_from_response(self, response: dict[str, Any]) -> None:
        self.latest_response = response
        if "entry" in response:
            self.entry = response["entry"]
        if "notebook" in response:
            self.notebook = response["notebook"]
        if "version" in response:
            self.version = response["version"]
        if "versions" in response:
            self.versions = response["versions"]
            matching = [
                item
                for item in self.versions
                if int(item.get("version", -1)) == self.version_number
            ]
            if matching:
                self.version = matching[0]


class GoblinKingNotebookClient:
    """Tiny HTTP client intended for JupyterHub workbooks."""

    def __init__(
        self,
        api_url: str | None = None,
        token: str | None = None,
        repository_url: str | None = None,
        request_timeout_seconds: float = 120.0,
    ) -> None:
        self.api_url = (api_url or os.environ.get("GOBLIN_KING_API_URL") or "").rstrip("/")
        if not self.api_url:
            self.api_url = "http://127.0.0.1:8000"
        configured_repository_url = (
            repository_url or os.environ.get("GOBLIN_KING_REPOSITORY_URL") or ""
        ).rstrip("/")
        self.repository_url = configured_repository_url or None
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

    def declare_service(
        self,
        *,
        source: str,
        kind: str,
        app_name: str = "app",
        requirements: list[str] | None = None,
        display_name: str | None = None,
        project_id: str | None = None,
        image: str | None = None,
        port: int = 8080,
        probe_path: str = "/hello",
        metadata: dict[str, Any] | None = None,
    ) -> NotebookASGIService:
        """Declare notebook source as a managed ASGI service bundle."""
        record = self._request(
            "POST",
            "/notebooks/services",
            {
                "kind": kind,
                "display_name": display_name or kind,
                "project_id": project_id,
                "image": image,
                "source": source,
                "app_name": app_name,
                "requirements": requirements or [],
                "port": port,
                "probe_path": probe_path,
                "metadata": metadata or {},
            },
        )
        return NotebookASGIService(client=self, record=record)

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

    def validate_service(self, kind: str, *, timeout_seconds: int = 120) -> dict[str, Any]:
        """Validate a declared notebook ASGI service."""
        return self._request(
            "POST",
            f"/notebooks/services/{urlparse.quote(kind, safe='')}/validate",
            {"timeout_seconds": timeout_seconds},
        )

    def start_service(self, kind: str, *, timeout_seconds: int = 120) -> dict[str, Any]:
        """Start a declared notebook ASGI service."""
        return self._request(
            "POST",
            f"/notebooks/services/{urlparse.quote(kind, safe='')}/start",
            {"timeout_seconds": timeout_seconds},
        )

    def stop_service(self, kind: str) -> dict[str, Any]:
        """Stop a declared notebook ASGI service."""
        return self._request(
            "POST",
            f"/notebooks/services/{urlparse.quote(kind, safe='')}/stop",
        )

    def submit_repository_function(
        self,
        function: Callable[..., Any] | None = None,
        *,
        name: str,
        source: str | None = None,
        function_name: str | None = None,
        display_name: str | None = None,
        description: str | None = None,
        tags: list[str] | None = None,
        project_id: str | None = None,
        image: str | None = None,
        timeout_seconds: int | None = None,
        max_retries: int = 0,
        metadata: dict[str, Any] | None = None,
    ) -> RepositoryEntryHandle:
        """Submit a notebook-defined function to the optional repository."""
        if function is None and source is None:
            raise ValueError("function or source is required")
        resolved_function_name = function_name or (function.__name__ if function else "run")
        response = self._repository_request(
            "POST",
            "/repository/entries",
            {
                "name": name,
                "type": "notebook_function",
                "source": source or _function_source(function),  # type: ignore[arg-type]
                "function_name": resolved_function_name,
                "display_name": display_name or name,
                "description": description,
                "tags": tags or [],
                "project_id": project_id,
                "image": image,
                "timeout_seconds": timeout_seconds,
                "max_retries": max_retries,
                "metadata": metadata or {},
            },
        )
        return RepositoryEntryHandle(
            client=self,
            entry=response["entry"],
            version=response["version"],
            notebook=response.get("notebook"),
            latest_response=response,
        )

    def submit_repository_service(
        self,
        *,
        source: str,
        name: str,
        app_name: str = "app",
        requirements: list[str] | None = None,
        display_name: str | None = None,
        description: str | None = None,
        tags: list[str] | None = None,
        project_id: str | None = None,
        image: str | None = None,
        port: int = 8080,
        probe_path: str = "/hello",
        metadata: dict[str, Any] | None = None,
    ) -> RepositoryEntryHandle:
        """Submit notebook ASGI service source to the optional repository."""
        response = self._repository_request(
            "POST",
            "/repository/entries",
            {
                "name": name,
                "type": "notebook_service",
                "source": source,
                "app_name": app_name,
                "requirements": requirements or [],
                "display_name": display_name or name,
                "description": description,
                "tags": tags or [],
                "project_id": project_id,
                "image": image,
                "port": port,
                "probe_path": probe_path,
                "metadata": metadata or {},
            },
        )
        return RepositoryEntryHandle(
            client=self,
            entry=response["entry"],
            version=response["version"],
            notebook=response.get("notebook"),
            latest_response=response,
        )

    def submit_directory_function(
        self,
        function: Callable[..., Any] | None = None,
        *,
        name: str,
        source: str | None = None,
        function_name: str | None = None,
        display_name: str | None = None,
        description: str | None = None,
        tags: list[str] | None = None,
        project_id: str | None = None,
        image: str | None = None,
        timeout_seconds: int | None = None,
        max_retries: int = 0,
        metadata: dict[str, Any] | None = None,
    ) -> RepositoryEntryHandle:
        """Submit a notebook-defined function to the optional Goblin Directory."""
        return self.submit_repository_function(
            function,
            name=name,
            source=source,
            function_name=function_name,
            display_name=display_name,
            description=description,
            tags=tags,
            project_id=project_id,
            image=image,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            metadata=metadata,
        )

    def submit_directory_service(
        self,
        *,
        source: str,
        name: str,
        app_name: str = "app",
        requirements: list[str] | None = None,
        display_name: str | None = None,
        description: str | None = None,
        tags: list[str] | None = None,
        project_id: str | None = None,
        image: str | None = None,
        port: int = 8080,
        probe_path: str = "/hello",
        metadata: dict[str, Any] | None = None,
    ) -> RepositoryEntryHandle:
        """Submit notebook ASGI service source to the optional Goblin Directory."""
        return self.submit_repository_service(
            source=source,
            name=name,
            app_name=app_name,
            requirements=requirements,
            display_name=display_name,
            description=description,
            tags=tags,
            project_id=project_id,
            image=image,
            port=port,
            probe_path=probe_path,
            metadata=metadata,
        )

    def get_repository_entry(self, entry_id: str) -> dict[str, Any]:
        """Inspect one repository entry visible to this caller."""
        return self._repository_request(
            "GET",
            f"/repository/entries/{urlparse.quote(entry_id, safe='')}",
        )

    def get_directory_entry(self, entry_id: str) -> dict[str, Any]:
        """Inspect one directory entry visible to this caller."""
        return self.get_repository_entry(entry_id)

    def list_repository_entries(
        self,
        *,
        project_id: str | None = None,
        entry_type: str | None = None,
        status: str | None = "published",
        q: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        """List or search repository entries visible to this caller."""
        query: dict[str, str] = {
            "limit": str(limit),
            "offset": str(offset),
        }
        if project_id is not None:
            query["project_id"] = project_id
        if entry_type is not None:
            query["type"] = entry_type
        if status is not None:
            query["status"] = status
        if q:
            query["q"] = q
        return self._repository_request(
            "GET",
            f"/repository/entries?{urlparse.urlencode(query)}",
        )

    def list_directory_entries(
        self,
        *,
        project_id: str | None = None,
        entry_type: str | None = None,
        status: str | None = "published",
        q: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        """List or search directory entries visible to this caller."""
        return self.list_repository_entries(
            project_id=project_id,
            entry_type=entry_type,
            status=status,
            q=q,
            limit=limit,
            offset=offset,
        )

    def search_repository_entries(
        self,
        q: str,
        **filters: Any,
    ) -> dict[str, Any]:
        """Search repository entries with the same filters as list."""
        return self.list_repository_entries(q=q, **filters)

    def search_directory_entries(self, q: str, **filters: Any) -> dict[str, Any]:
        """Search directory entries with the same filters as list."""
        return self.list_directory_entries(q=q, **filters)

    def validate_repository_entry(
        self,
        entry_id: str,
        payload: dict[str, Any] | None = None,
        *,
        require_success: bool = True,
        timeout_seconds: int | None = None,
        version: int | None = None,
        progress: bool = False,
        on_progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        """Validate one submitted repository version."""
        started_at = time.monotonic()
        context = {"entry": {"id": entry_id}, "version": {"version": version}}
        self._emit_repository_progress(
            phase="validating",
            detail=context,
            elapsed_seconds=0.0,
            progress=progress,
            on_progress=on_progress,
        )
        try:
            response = self._repository_request(
                "POST",
                self._repository_entry_path(entry_id, "validate", version=version),
                {
                    "input": payload or {},
                    "require_success": require_success,
                    "timeout_seconds": timeout_seconds,
                },
            )
        except Exception:
            self._emit_repository_progress(
                phase="failed",
                detail=context,
                elapsed_seconds=time.monotonic() - started_at,
                progress=progress,
                on_progress=on_progress,
            )
            raise
        self._emit_repository_progress(
            phase="validated",
            detail=response,
            elapsed_seconds=time.monotonic() - started_at,
            progress=progress,
            on_progress=on_progress,
        )
        return response

    def validate_directory_entry(
        self,
        entry_id: str,
        payload: dict[str, Any] | None = None,
        *,
        require_success: bool = True,
        timeout_seconds: int | None = None,
        version: int | None = None,
        progress: bool = False,
        on_progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        """Validate one submitted directory version."""
        return self.validate_repository_entry(
            entry_id,
            payload,
            require_success=require_success,
            timeout_seconds=timeout_seconds,
            version=version,
            progress=progress,
            on_progress=on_progress,
        )

    def request_repository_review(
        self,
        entry_id: str,
        *,
        note: str | None = None,
        version: int | None = None,
        progress: bool = False,
        on_progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        """Request review for one validated repository version."""
        return self._repository_transition(
            entry_id,
            "request-review",
            {"note": note},
            version=version,
            phase="review_requested",
            progress=progress,
            on_progress=on_progress,
        )

    def request_directory_review(
        self,
        entry_id: str,
        *,
        note: str | None = None,
        version: int | None = None,
        progress: bool = False,
        on_progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        """Request review for one validated directory version."""
        return self.request_repository_review(
            entry_id,
            note=note,
            version=version,
            progress=progress,
            on_progress=on_progress,
        )

    def approve_repository_entry(
        self,
        entry_id: str,
        *,
        note: str | None = None,
        version: int | None = None,
        progress: bool = False,
        on_progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        """Approve a repository version with an admin token."""
        return self._repository_transition(
            entry_id,
            "approve",
            {"note": note},
            version=version,
            phase="approved",
            progress=progress,
            on_progress=on_progress,
        )

    def approve_directory_entry(
        self,
        entry_id: str,
        *,
        note: str | None = None,
        version: int | None = None,
        progress: bool = False,
        on_progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        """Approve a directory version with an admin token."""
        return self.approve_repository_entry(
            entry_id,
            note=note,
            version=version,
            progress=progress,
            on_progress=on_progress,
        )

    def reject_repository_entry(
        self,
        entry_id: str,
        *,
        note: str | None = None,
        version: int | None = None,
        progress: bool = False,
        on_progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        """Reject a repository version with an admin token."""
        return self._repository_transition(
            entry_id,
            "reject",
            {"note": note},
            version=version,
            phase="rejected",
            progress=progress,
            on_progress=on_progress,
        )

    def reject_directory_entry(
        self,
        entry_id: str,
        *,
        note: str | None = None,
        version: int | None = None,
        progress: bool = False,
        on_progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        """Reject a directory version with an admin token."""
        return self.reject_repository_entry(
            entry_id,
            note=note,
            version=version,
            progress=progress,
            on_progress=on_progress,
        )

    def publish_repository_entry(
        self,
        entry_id: str,
        *,
        version: int | None = None,
        progress: bool = False,
        on_progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        """Publish an approved repository version with an admin token."""
        started_at = time.monotonic()
        response = self._repository_request(
            "POST",
            f"/repository/entries/{urlparse.quote(entry_id, safe='')}/publish",
            {"version": version},
        )
        self._emit_repository_progress(
            phase="published",
            detail=response,
            elapsed_seconds=time.monotonic() - started_at,
            progress=progress,
            on_progress=on_progress,
        )
        return response

    def publish_directory_entry(
        self,
        entry_id: str,
        *,
        version: int | None = None,
        progress: bool = False,
        on_progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        """Publish an approved directory version with an admin token."""
        return self.publish_repository_entry(
            entry_id,
            version=version,
            progress=progress,
            on_progress=on_progress,
        )

    def retire_repository_entry(
        self,
        entry_id: str,
        *,
        note: str | None = None,
        progress: bool = False,
        on_progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        """Retire a repository entry with an admin token."""
        return self._repository_transition(
            entry_id,
            "retire",
            {"note": note},
            phase="retired",
            progress=progress,
            on_progress=on_progress,
        )

    def retire_directory_entry(
        self,
        entry_id: str,
        *,
        note: str | None = None,
        progress: bool = False,
        on_progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        """Retire a directory entry with an admin token."""
        return self.retire_repository_entry(
            entry_id,
            note=note,
            progress=progress,
            on_progress=on_progress,
        )

    def wait_for_repository_status(
        self,
        entry_id: str,
        *,
        status: str = "published",
        timeout_seconds: int = 300,
        poll_seconds: float = 5.0,
        progress: bool = False,
        on_progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        """Poll a repository entry until it reaches the requested status."""
        started_at = time.monotonic()
        deadline = started_at + timeout_seconds
        latest: dict[str, Any] = {"entry": {"id": entry_id}}
        while time.monotonic() < deadline:
            latest = self.get_repository_entry(entry_id)
            entry_status = latest.get("entry", {}).get("status")
            now = time.monotonic()
            phase = "completed" if entry_status == status else "waiting"
            self._emit_repository_progress(
                phase=phase,
                detail=latest,
                elapsed_seconds=now - started_at,
                progress=progress,
                on_progress=on_progress,
            )
            if entry_status == status:
                return latest
            time.sleep(poll_seconds)
        self._emit_repository_progress(
            phase="timed_out",
            detail=latest,
            elapsed_seconds=time.monotonic() - started_at,
            progress=progress,
            on_progress=on_progress,
        )
        raise TimeoutError(f"timed out waiting for repository entry {entry_id} to be {status}")

    def wait_for_directory_status(
        self,
        entry_id: str,
        *,
        status: str = "published",
        timeout_seconds: int = 300,
        poll_seconds: float = 5.0,
        progress: bool = False,
        on_progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        """Poll a directory entry until it reaches the requested status."""
        return self.wait_for_repository_status(
            entry_id,
            status=status,
            timeout_seconds=timeout_seconds,
            poll_seconds=poll_seconds,
            progress=progress,
            on_progress=on_progress,
        )

    def run_repository_function(
        self,
        name: str,
        payload: dict[str, Any] | None = None,
        *,
        input: dict[str, Any] | None = None,
        project_id: str | None = None,
        version: int | None = None,
        wait: bool = True,
        timeout_seconds: int = 120,
        poll_seconds: float = 1.0,
        progress: bool = False,
        progress_interval_seconds: float = 5.0,
        on_progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        """Run an approved repository function by project-local name."""
        if payload is not None and input is not None:
            raise ValueError("pass either payload or input, not both")
        request_input = input if input is not None else payload
        response = self._repository_request(
            "POST",
            f"/repository/functions/{urlparse.quote(name, safe='')}/run",
            {
                "input": request_input or {},
                "project_id": project_id,
                "version": version,
            },
        )
        job = response["job"]
        kind = str(response["version"]["kind"])
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
            return {**response, "run": None}
        run_result = self._wait_for_job(
            job,
            kind,
            started_at=start,
            timeout_seconds=timeout_seconds,
            poll_seconds=poll_seconds,
            progress=progress,
            progress_interval_seconds=progress_interval_seconds,
            on_progress=on_progress,
        )
        return {**response, **run_result}

    def run_directory_function(
        self,
        name: str,
        payload: dict[str, Any] | None = None,
        *,
        input: dict[str, Any] | None = None,
        project_id: str | None = None,
        version: int | None = None,
        wait: bool = True,
        timeout_seconds: int = 120,
        poll_seconds: float = 1.0,
        progress: bool = False,
        progress_interval_seconds: float = 5.0,
        on_progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        """Run an approved directory function by project-local name."""
        return self.run_repository_function(
            name,
            payload,
            input=input,
            project_id=project_id,
            version=version,
            wait=wait,
            timeout_seconds=timeout_seconds,
            poll_seconds=poll_seconds,
            progress=progress,
            progress_interval_seconds=progress_interval_seconds,
            on_progress=on_progress,
        )

    def repository_service(
        self,
        name: str,
        *,
        project_id: str | None = None,
        version: int | None = None,
    ) -> RepositoryASGIService:
        """Return a handle for an approved repository service by name."""
        return RepositoryASGIService(
            client=self,
            name=name,
            project_id=project_id,
            version=version,
        )

    def directory_service(
        self,
        name: str,
        *,
        project_id: str | None = None,
        version: int | None = None,
    ) -> RepositoryASGIService:
        """Return a handle for an approved directory service by name."""
        return self.repository_service(name, project_id=project_id, version=version)

    def start_repository_service(
        self,
        name: str,
        *,
        project_id: str | None = None,
        version: int | None = None,
        timeout_seconds: int = 120,
    ) -> dict[str, Any]:
        """Start an approved repository service by name."""
        return self._repository_request(
            "POST",
            f"/repository/services/{urlparse.quote(name, safe='')}/start",
            {
                "project_id": project_id,
                "version": version,
                "timeout_seconds": timeout_seconds,
            },
        )

    def probe_repository_service(
        self,
        name: str,
        *,
        project_id: str | None = None,
        version: int | None = None,
    ) -> dict[str, Any]:
        """Probe an approved repository service by name."""
        return self._repository_request(
            "POST",
            f"/repository/services/{urlparse.quote(name, safe='')}/probe",
            {"project_id": project_id, "version": version},
        )

    def proxy_repository_service(
        self,
        name: str,
        path: str,
        *,
        project_id: str | None = None,
        version: int | None = None,
    ) -> dict[str, Any]:
        """Proxy one GET request to an approved repository service by name."""
        clean_path = path if path.startswith("/") else f"/{path}"
        query: dict[str, str] = {}
        if project_id is not None:
            query["project_id"] = project_id
        if version is not None:
            query["version"] = str(version)
        suffix = ""
        if query:
            suffix = f"?{urlparse.urlencode(query)}"
        return self._repository_request(
            "GET",
            f"/repository/services/{urlparse.quote(name, safe='')}/proxy"
            f"{urlparse.quote(clean_path, safe='/')}{suffix}",
        )

    def stop_repository_service(
        self,
        name: str,
        *,
        project_id: str | None = None,
        version: int | None = None,
    ) -> dict[str, Any]:
        """Stop an approved repository service by name."""
        return self._repository_request(
            "POST",
            f"/repository/services/{urlparse.quote(name, safe='')}/stop",
            {"project_id": project_id, "version": version},
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
        return self._wait_for_job(
            job,
            kind,
            started_at=start,
            timeout_seconds=timeout_seconds,
            poll_seconds=poll_seconds,
            progress=progress,
            progress_interval_seconds=progress_interval_seconds,
            on_progress=on_progress,
        )

    def _wait_for_job(
        self,
        job: dict[str, Any],
        kind: str,
        *,
        started_at: float,
        timeout_seconds: int,
        poll_seconds: float,
        progress: bool,
        progress_interval_seconds: float,
        on_progress: ProgressCallback | None,
    ) -> dict[str, Any]:
        """Poll one submitted job until it reaches a terminal state."""
        deadline = started_at + timeout_seconds
        next_progress = started_at + progress_interval_seconds
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
                    elapsed_seconds=now - started_at,
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
                    elapsed_seconds=now - started_at,
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

    def _emit_service_progress(
        self,
        *,
        phase: str,
        kind: str,
        service: dict[str, Any],
        elapsed_seconds: float,
        progress: bool,
        on_progress: ProgressCallback | None,
    ) -> None:
        """Emit one optional notebook-friendly service progress update."""
        if not progress and on_progress is None:
            return
        payload = {
            "phase": phase,
            "kind": kind,
            "service_id": service.get("active_service_id"),
            "runtime_status": service.get("runtime_status"),
            "runtime_backend": service.get("runtime_backend"),
            "runtime_name": service.get("runtime_name"),
            "elapsed_seconds": round(elapsed_seconds, 1),
        }
        if on_progress is not None:
            on_progress(payload)
        elif progress:
            service_id = payload["service_id"] or "none"
            runtime = payload["runtime_status"] or phase
            print(f"[{payload['elapsed_seconds']}s] {kind} service={service_id} status={runtime}")

    def _emit_repository_progress(
        self,
        *,
        phase: str,
        detail: dict[str, Any],
        elapsed_seconds: float,
        progress: bool,
        on_progress: ProgressCallback | None,
    ) -> None:
        """Emit one optional notebook-friendly repository progress update."""
        if not progress and on_progress is None:
            return
        entry = detail.get("entry") or {}
        versions = detail.get("versions") or []
        version = detail.get("version") or (versions[-1] if versions else {})
        payload = {
            "phase": phase,
            "entry_id": entry.get("id"),
            "name": entry.get("name"),
            "entry_status": entry.get("status"),
            "project_id": entry.get("project_id"),
            "version": version.get("version"),
            "version_status": version.get("status"),
            "kind": version.get("kind"),
            "elapsed_seconds": round(elapsed_seconds, 1),
        }
        if on_progress is not None:
            on_progress(payload)
        elif progress:
            name = payload["name"] or payload["entry_id"] or "repository-entry"
            entry_status = payload["entry_status"] or phase
            version_status = payload["version_status"] or "unknown"
            print(
                f"[{payload['elapsed_seconds']}s] repository {name} "
                f"entry={entry_status} version={version_status}"
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
        *,
        base_url: str | None = None,
    ) -> dict[str, Any]:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.token}",
        }
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = urlrequest.Request(
            f"{base_url or self.api_url}{path}",
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with urlrequest.urlopen(request, timeout=self.request_timeout_seconds) as response:
                raw = response.read().decode("utf-8")
        except urlerror.HTTPError as error:
            body = error.read().decode("utf-8", errors="replace")
            hint = ""
            if error.code == 404 and path.startswith("/notebooks/"):
                hint = (
                    " The notebook helper is newer than the Goblin King API at "
                    f"{self.api_url}, or GOBLIN_KING_API_URL points at the wrong service. "
                    "Redeploy Goblin King from the matching branch, then rerun the workbook."
                )
            if error.code == 404 and path.startswith("/repository/"):
                request_base_url = base_url or self.api_url
                hint = (
                    " Repository routes are unavailable at "
                    f"{request_base_url}. Enable repository.enabled=true on the API, "
                    "or set GOBLIN_KING_REPOSITORY_URL to the optional repository service."
                )
            raise RuntimeError(
                f"{method} {path} failed with {error.code}: {body}{hint}"
            ) from error
        return json.loads(raw) if raw else {}

    def _repository_request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send a repository request to the configured repository endpoint."""
        return self._request(
            method,
            path,
            payload,
            base_url=self.repository_url or self.api_url,
        )

    def _repository_transition(
        self,
        entry_id: str,
        action: str,
        payload: dict[str, Any],
        *,
        version: int | None = None,
        phase: str,
        progress: bool,
        on_progress: ProgressCallback | None,
    ) -> dict[str, Any]:
        started_at = time.monotonic()
        response = self._repository_request(
            "POST",
            self._repository_entry_path(entry_id, action, version=version),
            payload,
        )
        self._emit_repository_progress(
            phase=phase,
            detail=response,
            elapsed_seconds=time.monotonic() - started_at,
            progress=progress,
            on_progress=on_progress,
        )
        return response

    def _repository_entry_path(
        self,
        entry_id: str,
        action: str,
        *,
        version: int | None = None,
    ) -> str:
        path = f"/repository/entries/{urlparse.quote(entry_id, safe='')}/{action}"
        if version is not None:
            path = f"{path}?{urlparse.urlencode({'version': str(version)})}"
        return path


def _function_source(function: Callable[..., Any]) -> str:
    """Return notebook function source in a form the runner can execute."""
    try:
        return textwrap.dedent(inspect.getsource(function))
    except OSError as error:
        raise ValueError(
            "could not read function source; pass source=... when declaring this goblin"
        ) from error
