"""Human-facing local demo orchestration for trusted adopter onboarding."""

from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse, urlunparse
from urllib.request import Request, urlopen

from pydantic import BaseModel

from goblin_king.api_settings import ApiSettings
from goblin_king.jsonio import read_json_file
from goblin_king.project import ProjectSettings
from goblin_king.registry import GoblinRegistry
from goblin_king.store import SQLiteStore
from goblin_king.validation import WorkerValidationResult, validate_workers, validation_record
from goblin_king.workers import WorkerConfigError, WorkerImageMap

DEFAULT_DEMO_PROJECT = Path("examples/adopting-project/goblin-king-project.json")
DEFAULT_DEMO_KIND = "project.inline.hello"
DEFAULT_DEMO_INPUT = Path("examples/input.json")
DEFAULT_ADMIN_URL = "http://127.0.0.1:8080/admin"
DEFAULT_ADMIN_TOKEN = "local-dev-token"
DEFAULT_REDIS_URL = "redis://localhost:6379/0"
DEFAULT_TIMEOUT_SECONDS = 120
DEFAULT_POLL_SECONDS = 2.0

DEMO_COMPOSE_FILES = [
    Path("docker-compose.yml"),
    Path("examples/adopting-project/docker-compose.host-project.yml"),
]
DEMO_COMPOSE_PROFILES = ["api", "admin", "scheduler", "project-workers"]
DEMO_COMPOSE_SERVICES = [
    "redis",
    "api",
    "admin",
    "scheduler",
    "long-hello",
    "worker-project-maintenance-hello",
]
TERMINAL_JOB_STATUSES = {"completed", "failed", "timed_out", "cancelled"}


class CompletedCommand(BaseModel):
    """Captured shell command result for demo proof and tests."""

    args: list[str]
    returncode: int
    stdout: str = ""
    stderr: str = ""


class DemoProjectValidation(BaseModel):
    """Static project validation summary used by the demo flow."""

    goblins: int
    workers: int
    worker_coverage: str
    dockerfiles: str


class DemoResult(BaseModel):
    """Structured proof from `goblin-king demo up`."""

    ok: bool
    stage: str
    admin_url: str
    api_base_url: str
    project: str
    kind: str
    input: str
    cleanup: str
    compose: CompletedCommand | None = None
    project_validation: DemoProjectValidation | None = None
    validation: WorkerValidationResult | None = None
    discovery: dict[str, Any] | None = None
    job: dict[str, Any] | None = None
    run: dict[str, Any] | None = None
    error: str | None = None


class DemoDownResult(BaseModel):
    """Structured result from stopping the local demo stack."""

    ok: bool
    cleanup: str
    compose: CompletedCommand
    error: str | None = None


class CommandRunner(Protocol):
    """Minimal command runner protocol for testable shell boundaries."""

    def run(
        self,
        args: list[str],
        *,
        env: dict[str, str] | None = None,
        timeout_seconds: int | None = None,
    ) -> CompletedCommand:
        """Run a local command and return captured output."""


class JsonHttpClient(Protocol):
    """Minimal JSON HTTP client protocol for testable admin/API calls."""

    def request_json(
        self,
        method: str,
        url: str,
        *,
        token: str | None = None,
        payload: dict[str, Any] | None = None,
        timeout_seconds: int = 10,
    ) -> dict[str, Any]:
        """Send one HTTP request and parse a JSON object response."""


class SubprocessCommandRunner:
    """Run local commands through subprocess without invoking a shell."""

    def run(
        self,
        args: list[str],
        *,
        env: dict[str, str] | None = None,
        timeout_seconds: int | None = None,
    ) -> CompletedCommand:
        completed = subprocess.run(
            args,
            check=False,
            capture_output=True,
            text=True,
            env=env,
            timeout=timeout_seconds,
        )
        return CompletedCommand(
            args=args,
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )


class UrllibJsonHttpClient:
    """HTTP client for the local admin proxy and FastAPI control plane."""

    def request_json(
        self,
        method: str,
        url: str,
        *,
        token: str | None = None,
        payload: dict[str, Any] | None = None,
        timeout_seconds: int = 10,
    ) -> dict[str, Any]:
        headers = {"Accept": "application/json"}
        data = None
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if payload is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(payload).encode("utf-8")
        request = Request(url, data=data, headers=headers, method=method)
        with urlopen(request, timeout=timeout_seconds) as response:
            body = response.read().decode("utf-8")
        return json.loads(body) if body else {}


@dataclass(frozen=True)
class DemoContext:
    """Loaded project context shared by demo validation and diagnostics."""

    settings: ProjectSettings
    registry: GoblinRegistry
    workers: WorkerImageMap
    api_settings: ApiSettings


def run_demo_up(
    *,
    project: Path = DEFAULT_DEMO_PROJECT,
    kind: str = DEFAULT_DEMO_KIND,
    input_path: Path = DEFAULT_DEMO_INPUT,
    admin_url: str = DEFAULT_ADMIN_URL,
    token: str = DEFAULT_ADMIN_TOKEN,
    redis_url: str = DEFAULT_REDIS_URL,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    poll_seconds: float = DEFAULT_POLL_SECONDS,
    command_runner: CommandRunner | None = None,
    http_client: JsonHttpClient | None = None,
) -> DemoResult:
    """Start the local stack, validate one project goblin, and prove an admin-visible run."""
    project = Path(project)
    input_path = Path(input_path)
    api_base_url = admin_api_base_url(admin_url)
    cleanup = "goblin-king demo down"
    runner = command_runner or SubprocessCommandRunner()
    client = http_client or UrllibJsonHttpClient()
    base = {
        "admin_url": admin_url,
        "api_base_url": api_base_url,
        "project": str(project),
        "kind": kind,
        "input": str(input_path),
        "cleanup": cleanup,
    }
    try:
        context = load_demo_context(project)
        compose = compose_up(runner, project)
        project_validation = validate_demo_project(context)
        validation = validate_demo_worker(
            context,
            kind=kind,
            input_path=input_path,
            redis_url=redis_url,
        )
        wait_for_admin_api(client, api_base_url, timeout_seconds=timeout_seconds)
        discovery = client.request_json(
            "POST",
            f"{api_base_url}/admin/discovery/reload",
            token=token,
            timeout_seconds=10,
        )
        job = client.request_json(
            "POST",
            f"{api_base_url}/jobs",
            token=token,
            payload={"kind": kind, "input": read_input(input_path)},
            timeout_seconds=10,
        )
        final_job, run = wait_for_job_run(
            client,
            api_base_url,
            token=token,
            job=job,
            kind=kind,
            timeout_seconds=timeout_seconds,
            poll_seconds=poll_seconds,
        )
        ok = final_job.get("status") == "completed" and (run or {}).get("status") == "completed"
        return DemoResult(
            ok=ok,
            stage="complete" if ok else "run",
            compose=compose,
            project_validation=project_validation,
            validation=validation,
            discovery=discovery,
            job=final_job,
            run=run,
            error=None if ok else final_job.get("last_error") or (run or {}).get("error"),
            **base,
        )
    except Exception as error:  # noqa: BLE001 - this is a user-facing proof command.
        return DemoResult(ok=False, stage="failed", error=str(error), **base)


def run_demo_down(
    *,
    project: Path = DEFAULT_DEMO_PROJECT,
    command_runner: CommandRunner | None = None,
) -> DemoDownResult:
    """Stop the local Docker Compose demo stack."""
    runner = command_runner or SubprocessCommandRunner()
    command = compose_command(["down"])
    env = compose_environment(project)
    result = runner.run(command, env=env)
    return DemoDownResult(
        ok=result.returncode == 0,
        cleanup="stopped",
        compose=result,
        error=None if result.returncode == 0 else result.stderr or result.stdout,
    )


def load_demo_context(project: Path) -> DemoContext:
    """Load project settings, registry, workers, and API settings for the demo."""
    settings = ProjectSettings.from_path(project)
    registry = GoblinRegistry.from_project_sources(
        settings.registries,
        include_entry_points=settings.entry_points,
        definitions=settings.registry_definitions(),
    )
    workers = WorkerImageMap.from_path_and_definitions(
        settings.images,
        settings.worker_definitions(),
    )
    api_settings = ApiSettings.from_path(settings.api_settings)
    return DemoContext(
        settings=settings,
        registry=registry,
        workers=workers,
        api_settings=api_settings,
    )


def validate_demo_project(context: DemoContext) -> DemoProjectValidation:
    """Validate worker coverage and Dockerfile presence for every project goblin."""
    missing: list[str] = []
    invalid: list[str] = []
    definitions = context.registry.list()
    for definition in definitions:
        try:
            worker = context.workers.get(definition.kind)
        except WorkerConfigError:
            missing.append(definition.kind)
            continue
        dockerfile = context.workers.resolved_context(worker) / worker.dockerfile
        if not dockerfile.exists():
            invalid.append(f"{definition.kind}: {dockerfile}")
    if missing or invalid:
        details = []
        if missing:
            details.append("missing worker mapping: " + ", ".join(missing))
        if invalid:
            details.append("missing Dockerfile: " + ", ".join(invalid))
        raise ValueError("; ".join(details))
    return DemoProjectValidation(
        goblins=len(definitions),
        workers=len(context.workers.items()),
        worker_coverage=f"{len(definitions)}/{len(definitions)}",
        dockerfiles="ok",
    )


def validate_demo_worker(
    context: DemoContext,
    *,
    kind: str,
    input_path: Path,
    redis_url: str,
) -> WorkerValidationResult:
    """Validate and persist one worker proof into the project API database."""
    results = validate_workers(
        registry=context.registry,
        workers=context.workers,
        input_payload=read_input(input_path),
        kinds=[kind],
        build=True,
        require_success=True,
        redis_url=redis_url,
    )
    if len(results) != 1:
        raise ValueError(f"expected one validation result for {kind}, got {len(results)}")
    result = results[0]
    SQLiteStore(context.api_settings.db).save_worker_validation(validation_record(result))
    if not result.ok:
        raise ValueError(result.error or f"worker validation failed for {kind}")
    return result


def compose_up(runner: CommandRunner, project: Path) -> CompletedCommand:
    """Start the trusted local Docker Compose admin/adopter stack."""
    result = runner.run(
        compose_command(["up", "-d", "--build", *DEMO_COMPOSE_SERVICES]),
        env=compose_environment(project),
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout or "docker compose up failed")
    return result


def compose_command(action: list[str]) -> list[str]:
    """Return the Docker Compose command used by demo up/down."""
    command = ["docker", "compose"]
    for compose_file in DEMO_COMPOSE_FILES:
        command.extend(["-f", str(compose_file)])
    for profile in DEMO_COMPOSE_PROFILES:
        command.extend(["--profile", profile])
    command.extend(action)
    return command


def compose_environment(project: Path) -> dict[str, str]:
    """Return environment for the adopter project Compose overlay."""
    env = dict(os.environ)
    env["HOST_PROJECT_PATH"] = str(Path(project).resolve().parent)
    return env


def admin_api_base_url(admin_url: str) -> str:
    """Translate an admin page URL into the proxied admin API base URL."""
    parsed = urlparse(admin_url)
    path = parsed.path.rstrip("/")
    if path.endswith("/admin"):
        api_path = f"{path.removesuffix('/admin')}/admin-api"
    else:
        api_path = f"{path}/admin-api"
    return urlunparse(parsed._replace(path=api_path, params="", query="", fragment="")).rstrip("/")


def wait_for_admin_api(
    client: JsonHttpClient,
    api_base_url: str,
    *,
    timeout_seconds: int,
) -> dict[str, Any]:
    """Poll the admin-proxied API health endpoint until it is reachable."""
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() <= deadline:
        try:
            return client.request_json("GET", f"{api_base_url}/health", timeout_seconds=5)
        except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as error:
            last_error = error
            time.sleep(1)
    raise TimeoutError(f"admin API did not become healthy: {last_error}")


def wait_for_job_run(
    client: JsonHttpClient,
    api_base_url: str,
    *,
    token: str,
    job: dict[str, Any],
    kind: str,
    timeout_seconds: int,
    poll_seconds: float,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Poll job and run endpoints until the submitted job reaches a terminal state."""
    job_id = str(job.get("id") or "")
    if not job_id:
        raise ValueError("job response did not include an id")
    deadline = time.monotonic() + timeout_seconds
    latest_job = job
    latest_run: dict[str, Any] | None = None
    while time.monotonic() <= deadline:
        latest_job = client.request_json(
            "GET",
            f"{api_base_url}/jobs/{quote(job_id)}",
            token=token,
            timeout_seconds=10,
        )
        runs = client.request_json(
            "GET",
            f"{api_base_url}/runs?kind={quote(kind)}&limit=20",
            token=token,
            timeout_seconds=10,
        )
        latest_run = next(
            (
                item
                for item in runs.get("items", [])
                if isinstance(item, dict) and item.get("job_id") == job_id
            ),
            None,
        )
        if latest_job.get("status") in TERMINAL_JOB_STATUSES and latest_run is not None:
            return latest_job, latest_run
        time.sleep(poll_seconds)
    raise TimeoutError(f"job did not finish before timeout: {job_id}")


def read_input(path: Path) -> dict[str, Any]:
    """Read one JSON object input payload."""
    payload = read_json_file(path)
    if not isinstance(payload, dict):
        raise ValueError(f"input JSON must be an object: {path}")
    return payload
