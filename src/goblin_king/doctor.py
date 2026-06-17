"""Local environment diagnostics for Goblin King onboarding."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from urllib.error import HTTPError, URLError

from pydantic import BaseModel, Field
from redis import Redis
from redis.exceptions import RedisError

from goblin_king.demo import (
    DEFAULT_ADMIN_TOKEN,
    DEFAULT_ADMIN_URL,
    DEFAULT_DEMO_KIND,
    DEFAULT_DEMO_PROJECT,
    DEFAULT_REDIS_URL,
    CommandRunner,
    JsonHttpClient,
    SubprocessCommandRunner,
    UrllibJsonHttpClient,
    admin_api_base_url,
    load_demo_context,
    validate_demo_project,
)
from goblin_king.store import SQLiteStore


class DoctorCheck(BaseModel):
    """One diagnostic check with repair guidance."""

    name: str
    status: str
    message: str
    repair_command: str | None = None
    doc_link: str | None = None


class DoctorResult(BaseModel):
    """Structured diagnostics emitted by `goblin-king doctor`."""

    ok: bool
    project: str
    kind: str
    admin_url: str
    checks: list[DoctorCheck] = Field(default_factory=list)


def run_doctor(
    *,
    project: Path = DEFAULT_DEMO_PROJECT,
    kind: str = DEFAULT_DEMO_KIND,
    admin_url: str = DEFAULT_ADMIN_URL,
    token: str = DEFAULT_ADMIN_TOKEN,
    redis_url: str = DEFAULT_REDIS_URL,
    command_runner: CommandRunner | None = None,
    http_client: JsonHttpClient | None = None,
) -> DoctorResult:
    """Run local diagnostics for the demo/adopter onboarding path."""
    project = Path(project)
    runner = command_runner or SubprocessCommandRunner()
    client = http_client or UrllibJsonHttpClient()
    checks: list[DoctorCheck] = []
    context = None

    checks.append(
        DoctorCheck(
            name="python_package",
            status="pass",
            message="Goblin King imported successfully.",
        )
    )
    checks.extend(docker_checks(runner))

    try:
        context = load_demo_context(project)
        validate_demo_project(context)
        checks.append(
            DoctorCheck(
                name="project_config",
                status="pass",
                message=f"Project config is valid: {project}",
            )
        )
    except Exception as error:  # noqa: BLE001 - diagnostics should report all config errors.
        checks.append(
            DoctorCheck(
                name="project_config",
                status="fail",
                message=str(error),
                repair_command=f"goblin-king project validate --project {project}",
                doc_link="docs/project-template-quickstart.md",
            )
        )

    checks.append(redis_check(redis_url))
    checks.extend(admin_checks(client, admin_url, token))
    if context is not None:
        checks.append(validation_status_check(context.api_settings.db, kind, project))
    else:
        checks.append(
            DoctorCheck(
                name="validation_status",
                status="warn",
                message="Validation status skipped because project config could not be loaded.",
                repair_command=f"goblin-king project validate --project {project}",
            )
        )

    return DoctorResult(
        ok=not any(check.status == "fail" for check in checks),
        project=str(project),
        kind=kind,
        admin_url=admin_url,
        checks=checks,
    )


def docker_checks(runner: CommandRunner) -> list[DoctorCheck]:
    """Check Docker CLI, daemon, and Compose availability."""
    if shutil.which("docker") is None:
        return [
            DoctorCheck(
                name="docker_cli",
                status="fail",
                message="Docker CLI was not found on PATH.",
                repair_command="Install Docker Desktop or another local Docker engine.",
                doc_link="docs/testing-your-project-with-the-admin-panel.md",
            )
        ]
    checks = [
        command_check(
            runner,
            ["docker", "--version"],
            name="docker_cli",
            success_message="Docker CLI is available.",
            failure_message="Docker CLI did not run successfully.",
            repair_command="Install or repair Docker, then rerun goblin-king doctor.",
        ),
        command_check(
            runner,
            ["docker", "info"],
            name="docker_daemon",
            success_message="Docker daemon is reachable.",
            failure_message="Docker daemon is not reachable.",
            repair_command="Start Docker Desktop or your local Docker daemon.",
        ),
        command_check(
            runner,
            ["docker", "compose", "version"],
            name="docker_compose",
            success_message="Docker Compose is available.",
            failure_message="Docker Compose did not run successfully.",
            repair_command="Install Docker Compose v2 or repair your Docker install.",
        ),
    ]
    return checks


def command_check(
    runner: CommandRunner,
    args: list[str],
    *,
    name: str,
    success_message: str,
    failure_message: str,
    repair_command: str,
) -> DoctorCheck:
    """Run one diagnostic command and convert its exit code into a check."""
    result = runner.run(args, timeout_seconds=15)
    if result.returncode == 0:
        return DoctorCheck(name=name, status="pass", message=success_message)
    detail = result.stderr.strip() or result.stdout.strip() or failure_message
    return DoctorCheck(
        name=name,
        status="fail",
        message=f"{failure_message} {detail}",
        repair_command=repair_command,
    )


def redis_check(redis_url: str) -> DoctorCheck:
    """Check whether local Redis is reachable for validation and worker result transport."""
    try:
        Redis.from_url(redis_url).ping()
    except RedisError as error:
        return DoctorCheck(
            name="redis",
            status="warn",
            message=f"Redis is not reachable at {redis_url}: {error}",
            repair_command="docker compose up -d redis",
            doc_link="docs/demo-and-doctor-roadmap.md",
        )
    return DoctorCheck(name="redis", status="pass", message=f"Redis is reachable at {redis_url}.")


def admin_checks(
    client: JsonHttpClient,
    admin_url: str,
    token: str,
) -> list[DoctorCheck]:
    """Check admin page and proxied API reachability."""
    api_base = admin_api_base_url(admin_url)
    checks = []
    try:
        client.request_json("GET", f"{api_base}/health", timeout_seconds=5)
        checks.append(
            DoctorCheck(
                name="admin_api",
                status="pass",
                message=f"Admin-proxied API is reachable at {api_base}.",
            )
        )
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as error:
        checks.append(
            DoctorCheck(
                name="admin_api",
                status="warn",
                message=f"Admin-proxied API is not reachable at {api_base}: {error}",
                repair_command="goblin-king demo up",
                doc_link="docs/testing-your-project-with-the-admin-panel.md",
            )
        )
    try:
        client.request_json(
            "GET",
            f"{api_base}/goblins",
            token=token,
            timeout_seconds=5,
        )
        checks.append(
            DoctorCheck(
                name="admin_auth",
                status="pass",
                message="Admin API accepted the configured local token.",
            )
        )
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as error:
        checks.append(
            DoctorCheck(
                name="admin_auth",
                status="warn",
                message=f"Admin API auth/list check is not ready: {error}",
                repair_command="goblin-king demo up",
                doc_link="docs/testing-your-project-with-the-admin-panel.md",
            )
        )
    return checks


def validation_status_check(db_path: Path, kind: str, project: Path) -> DoctorCheck:
    """Inspect the latest persisted worker validation proof for one goblin kind."""
    validation = SQLiteStore(db_path).latest_worker_validation_for_kind(kind)
    if validation is None:
        return DoctorCheck(
            name="validation_status",
            status="warn",
            message=f"No validation proof has been recorded for {kind}.",
            repair_command=(
                f"goblin-king workers validate --project {project} "
                f"--input examples/input.json --kind {kind} --build --require-success"
            ),
            doc_link="docs/goblin-contract-validation.md",
        )
    if validation.status != "passed":
        return DoctorCheck(
            name="validation_status",
            status="warn",
            message=f"Latest validation for {kind} is {validation.status}.",
            repair_command=(
                f"goblin-king workers validate --project {project} "
                f"--input examples/input.json --kind {kind} --build --require-success"
            ),
            doc_link="docs/goblin-contract-validation.md",
        )
    return DoctorCheck(
        name="validation_status",
        status="pass",
        message=f"Latest validation proof for {kind} passed.",
    )
