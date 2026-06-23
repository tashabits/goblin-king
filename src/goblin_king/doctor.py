"""Local environment diagnostics for Goblin King onboarding."""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from urllib.error import HTTPError, URLError

from pydantic import BaseModel, Field
from redis import Redis
from redis.exceptions import RedisError

from goblin_king.contracts import utc_now
from goblin_king.demo import (
    DEFAULT_ADMIN_TOKEN,
    DEFAULT_ADMIN_URL,
    DEFAULT_DEMO_KIND,
    DEFAULT_DEMO_PROJECT,
    DEFAULT_REDIS_URL,
    DEMO_COMPOSE_FILES,
    CommandRunner,
    JsonHttpClient,
    SubprocessCommandRunner,
    UrllibJsonHttpClient,
    admin_api_base_url,
    load_demo_context,
    validate_demo_project,
)
from goblin_king.deployment import helm_template_command
from goblin_king.resource_policies import ResourcePolicyError, ResourcePolicySet
from goblin_king.runtime_helpers import docker_policy_args, kubernetes_policy_fields
from goblin_king.store import SQLiteStore
from goblin_king.validation import VALIDATOR_VERSION
from goblin_king.versions import GOBLIN_CONTAINER_CONTRACT_VERSION
from goblin_king.workers import WorkerConfigError

DoctorRuntimeSelection = Literal["docker", "kubernetes", "both"]
DEFAULT_HELM_CHART = Path("charts/goblin-king")


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
    runtime: DoctorRuntimeSelection = "docker",
    resource_policies: Path | None = None,
    helm_values: Path | None = None,
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
    try:
        runtime_modes = runtime_modes_for(runtime)
    except ValueError as error:
        checks.append(
            DoctorCheck(
                name="runtime_selection",
                status="fail",
                message=str(error),
            )
        )
        return DoctorResult(
            ok=False,
            project=str(project),
            kind=kind,
            admin_url=admin_url,
            checks=checks,
        )
    checks.append(
        DoctorCheck(
            name="runtime_selection",
            status="pass",
            message=f"Runtime diagnostics selected: {', '.join(runtime_modes)}.",
        )
    )
    if "docker" in runtime_modes:
        checks.extend(docker_checks(runner))
        checks.append(docker_socket_posture_check())
    if "kubernetes" in runtime_modes:
        checks.extend(kubernetes_checks(runner))
    if helm_values is not None:
        checks.append(helm_template_check(runner, helm_values=helm_values))

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

    if context is not None:
        checks.append(resource_policy_check(context, kind, resource_policies))
        checks.append(validation_coverage_check(context, runtime_modes, runner, project))
        checks.append(schedule_readiness_check(context, runtime_modes))
        checks.append(placement_runtime_check(context, kind, runtime_modes))
    else:
        checks.extend(
            [
                DoctorCheck(
                    name="resource_policy",
                    status="warn",
                    message=(
                        "Resource policy summary skipped because project config "
                        "could not be loaded."
                    ),
                    repair_command=f"goblin-king project validate --project {project}",
                ),
                DoctorCheck(
                    name="validation_coverage",
                    status="warn",
                    message=(
                        "Validation coverage skipped because project config "
                        "could not be loaded."
                    ),
                    repair_command=f"goblin-king project validate --project {project}",
                ),
                DoctorCheck(
                    name="schedule_readiness",
                    status="warn",
                    message=(
                        "Schedule readiness skipped because project config "
                        "could not be loaded."
                    ),
                    repair_command=f"goblin-king project validate --project {project}",
                ),
            ]
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


def runtime_modes_for(runtime: str) -> tuple[str, ...]:
    """Return concrete runtime modes selected by the doctor option."""
    if runtime == "both":
        return ("docker", "kubernetes")
    if runtime in {"docker", "kubernetes"}:
        return (runtime,)
    raise ValueError("doctor runtime must be docker, kubernetes, or both")


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


def kubernetes_checks(runner: CommandRunner) -> list[DoctorCheck]:
    """Check local Kubernetes client tooling without contacting a cluster."""
    checks = []
    try:
        __import__("kubernetes")
    except ImportError as error:
        checks.append(
            DoctorCheck(
                name="kubernetes_package",
                status="fail",
                message=f"Python Kubernetes client is not importable: {error}",
                repair_command="Install the project runtime dependencies.",
            )
        )
    else:
        checks.append(
            DoctorCheck(
                name="kubernetes_package",
                status="pass",
                message="Python Kubernetes client is importable.",
            )
        )

    if shutil.which("kubectl") is None:
        checks.append(
            DoctorCheck(
                name="kubectl_cli",
                status="warn",
                message=(
                    "kubectl was not found on PATH; Kubernetes runtime debugging "
                    "will be limited."
                ),
                repair_command="Install kubectl if you plan to inspect Kubernetes runtime jobs.",
            )
        )
        return checks

    checks.extend(
        [
            command_check(
                runner,
                ["kubectl", "version", "--client=true", "--output=json"],
                name="kubectl_cli",
                success_message="kubectl client is available.",
                failure_message="kubectl client did not run successfully.",
                repair_command="Install or repair kubectl.",
                failure_status="warn",
            ),
            command_check(
                runner,
                ["kubectl", "config", "current-context"],
                name="kubectl_context",
                success_message="kubectl has a current context configured.",
                failure_message="kubectl current context is not configured.",
                repair_command="Configure a kubeconfig context before using Kubernetes runtime.",
                failure_status="warn",
            ),
        ]
    )
    return checks


def docker_socket_posture_check(
    compose_files: list[Path] | None = None,
) -> DoctorCheck:
    """Inspect known local Compose files for Docker socket mounts."""
    inspected: list[str] = []
    socket_mounts: list[str] = []
    for path in compose_files or DEMO_COMPOSE_FILES:
        if not path.exists():
            continue
        inspected.append(str(path))
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if "/var/run/docker.sock" in text:
            socket_mounts.append(str(path))
    if not inspected:
        return DoctorCheck(
            name="docker_socket_posture",
            status="warn",
            message="No known Compose files were available for Docker socket posture inspection.",
        )
    if socket_mounts:
        return DoctorCheck(
            name="docker_socket_posture",
            status="warn",
            message=(
                "Docker socket mounts are present for local nested worker launch: "
                f"{', '.join(socket_mounts)}. Use only with trusted local project configs."
            ),
        )
    return DoctorCheck(
        name="docker_socket_posture",
        status="pass",
        message=f"No Docker socket mounts found in known Compose files: {', '.join(inspected)}.",
    )


def helm_template_check(
    runner: CommandRunner,
    *,
    helm_values: Path,
    chart: Path = DEFAULT_HELM_CHART,
) -> DoctorCheck:
    """Render the local Helm chart with an optional values file."""
    helm_values = Path(helm_values)
    if not helm_values.exists():
        return DoctorCheck(
            name="helm_template",
            status="fail",
            message=f"Helm values file was not found: {helm_values}",
            repair_command=f"Create the values file or rerun without --helm-values {helm_values}.",
        )
    if not chart.exists():
        return DoctorCheck(
            name="helm_template",
            status="fail",
            message=f"Helm chart was not found: {chart}",
        )
    if shutil.which("helm") is None:
        return DoctorCheck(
            name="helm_template",
            status="fail",
            message="Helm CLI was not found on PATH.",
            repair_command="Install Helm or rerun doctor without --helm-values.",
        )
    command = helm_template_command(chart=chart, release="goblin-king", values=helm_values)
    result = runner.run(command, timeout_seconds=30)
    if result.returncode == 0:
        return DoctorCheck(
            name="helm_template",
            status="pass",
            message=f"Helm template rendered successfully with values file: {helm_values}",
        )
    detail = result.stderr.strip() or result.stdout.strip() or "helm template failed"
    return DoctorCheck(
        name="helm_template",
        status="fail",
        message=f"Helm template rendering failed. {detail}",
        repair_command="Fix the chart values, then rerun the same doctor command.",
    )


def command_check(
    runner: CommandRunner,
    args: list[str],
    *,
    name: str,
    success_message: str,
    failure_message: str,
    repair_command: str,
    failure_status: str = "fail",
) -> DoctorCheck:
    """Run one diagnostic command and convert its exit code into a check."""
    result = runner.run(args, timeout_seconds=15)
    if result.returncode == 0:
        return DoctorCheck(name=name, status="pass", message=success_message)
    detail = result.stderr.strip() or result.stdout.strip() or failure_message
    return DoctorCheck(
        name=name,
        status=failure_status,
        message=f"{failure_message} {detail}",
        repair_command=repair_command,
    )


def resource_policy_check(
    context,
    kind: str,
    resource_policies: Path | None,
) -> DoctorCheck:
    """Summarize the effective resource policy for the selected kind."""
    try:
        policy_set = effective_resource_policy_set(context, resource_policies)
    except ResourcePolicyError as error:
        return DoctorCheck(
            name="resource_policy",
            status="fail",
            message=str(error),
            repair_command="Fix the resource policy file, then rerun doctor.",
        )
    except ValueError as error:
        return DoctorCheck(
            name="resource_policy",
            status="fail",
            message=str(error),
            repair_command="Adjust project resource defaults or operator ceilings.",
        )
    if policy_set is None:
        return DoctorCheck(
            name="resource_policy",
            status="warn",
            message="No resource policy is configured; runtime resource limits are not enforced.",
            repair_command="Add project resources or pass --resource-policies with a policy file.",
        )
    try:
        policy = policy_set.effective_for(kind)
    except (ResourcePolicyError, ValueError) as error:
        return DoctorCheck(
            name="resource_policy",
            status="fail",
            message=f"Effective resource policy for {kind} is invalid: {error}",
            repair_command="Adjust resource policy defaults, overrides, or ceilings.",
        )
    summary = {
        "kind": kind,
        "effective_policy": policy.compact(),
        "docker_args": docker_policy_args(policy),
        "kubernetes_fields": kubernetes_policy_fields(policy),
    }
    return DoctorCheck(
        name="resource_policy",
        status="pass",
        message="Effective resource policy summary: " + json.dumps(summary, sort_keys=True),
    )


def effective_resource_policy_set(
    context,
    resource_policies: Path | None,
) -> ResourcePolicySet | None:
    """Load operator policies, then layer project resource defaults and overrides."""
    operator_policies = None
    if resource_policies is not None:
        operator_policies = ResourcePolicySet.from_path(resource_policies)
    return context.settings.resource_policy_set(operator_policies)


def validation_coverage_check(
    context,
    runtime_modes: tuple[str, ...],
    runner: CommandRunner,
    project: Path,
) -> DoctorCheck:
    """Check every configured worker has current validation proof for selected runtimes."""
    definitions = context.registry.list()
    if not definitions:
        return DoctorCheck(
            name="validation_coverage",
            status="warn",
            message="No registered workload definitions were found for validation coverage.",
        )
    store = SQLiteStore(context.api_settings.db)
    required = 0
    passed: list[str] = []
    missing: list[str] = []
    stale: list[str] = []
    failed: list[str] = []
    image_errors: list[str] = []
    for definition in definitions:
        try:
            worker = context.workers.get(definition.kind)
        except WorkerConfigError as error:
            missing.append(f"{definition.kind}: {error}")
            continue
        for mode in runtime_modes:
            required += 1
            identity, identity_error = runtime_image_identity(
                mode,
                worker.image,
                runner,
            )
            label = f"{definition.kind}/{mode}"
            if identity_error is not None or identity is None:
                image_errors.append(f"{label}: {identity_error or 'image identity unavailable'}")
                continue
            validation = store.get_latest_worker_validation(
                kind=definition.kind,
                image_digest=identity,
                contract_version=GOBLIN_CONTAINER_CONTRACT_VERSION,
                validator_version=VALIDATOR_VERSION,
            )
            if validation is not None and validation.status == "passed":
                passed.append(label)
                continue
            latest = store.latest_worker_validation_for_kind(definition.kind)
            if latest is None:
                missing.append(f"{label}: no validation proof")
            elif latest.status != "passed":
                failed.append(f"{label}: latest proof is {latest.status}")
            else:
                stale.append(f"{label}: latest proof is not for the current runtime image")

    problems = missing + stale + failed + image_errors
    status = "pass" if required > 0 and len(passed) == required and not problems else "warn"
    message = (
        "Validation freshness and image coverage: "
        f"{len(passed)}/{required} selected runtime image identities have passing proof."
    )
    if problems:
        message += " " + summarize_items("Issues", problems)
    return DoctorCheck(
        name="validation_coverage",
        status=status,
        message=message,
        repair_command=(
            f"goblin-king workers validate --project {project} "
            "--input examples/input.json --build --require-success"
        )
        if status != "pass"
        else None,
        doc_link="docs/goblin-contract-validation.md" if status != "pass" else None,
    )


def runtime_image_identity(
    mode: str,
    image: str,
    runner: CommandRunner,
) -> tuple[str | None, str | None]:
    """Return the image identity used by the scheduler validation gate for one runtime."""
    if mode == "kubernetes":
        return f"kubernetes:{image}", None
    if mode != "docker":
        return None, f"unsupported runtime mode: {mode}"
    if shutil.which("docker") is None:
        return None, "Docker CLI was not found on PATH"
    result = runner.run(
        ["docker", "image", "inspect", image, "--format", "{{.Id}}"],
        timeout_seconds=15,
    )
    if result.returncode == 0:
        digest = result.stdout.strip()
        return (digest, None) if digest else (None, f"Docker did not report an image id: {image}")
    detail = result.stderr.strip() or result.stdout.strip() or "docker image inspect failed"
    return None, f"worker image unavailable: {image}; {detail}"


def schedule_readiness_check(
    context,
    runtime_modes: tuple[str, ...],
) -> DoctorCheck:
    """Inspect persisted and project-declared schedules for scheduler readiness."""
    definitions = {definition.kind: definition for definition in context.registry.list()}
    store = SQLiteStore(context.api_settings.db)
    schedules = store.list_schedules()
    enabled = [schedule for schedule in schedules if schedule.enabled]
    due = [schedule for schedule in enabled if ensure_utc(schedule.next_run_at) <= utc_now()]
    declared = [
        definition.kind
        for definition in definitions.values()
        if isinstance(definition.metadata.get("schedule"), dict)
        and bool(definition.metadata["schedule"])
    ]
    enabled_declared = [
        definition.kind
        for definition in definitions.values()
        if isinstance(definition.metadata.get("schedule"), dict)
        and definition.metadata["schedule"].get("enabled") is True
    ]
    unknown = sorted({schedule.kind for schedule in schedules if schedule.kind not in definitions})
    missing_workers: list[str] = []
    if any(mode in {"docker", "kubernetes"} for mode in runtime_modes):
        for schedule in enabled:
            if schedule.kind in definitions:
                try:
                    context.workers.get(schedule.kind)
                except WorkerConfigError as error:
                    missing_workers.append(f"{schedule.kind}: {error}")

    status = "fail" if unknown or missing_workers else "pass"
    details = (
        f"persisted={len(schedules)}, enabled={len(enabled)}, due={len(due)}, "
        f"project_declared={len(declared)}"
    )
    message = f"Schedule readiness: {details}."
    if enabled_declared:
        message += " " + summarize_items(
            "Enabled project-declared schedules",
            enabled_declared,
        )
    if unknown:
        message += " " + summarize_items("Unknown persisted schedule kinds", unknown)
    if missing_workers:
        message += " " + summarize_items("Worker mapping issues", missing_workers)
    return DoctorCheck(
        name="schedule_readiness",
        status=status,
        message=message,
        repair_command=f"goblin-king schedules list --db {context.api_settings.db}"
        if status != "pass"
        else None,
    )


def placement_runtime_check(
    context,
    kind: str,
    runtime_modes: tuple[str, ...],
) -> DoctorCheck:
    """Explain when project placement metadata cannot affect the selected runtime."""
    definition = context.registry.get(kind)
    placement = definition.metadata.get("placement")
    if not isinstance(placement, dict) or not any(placement.values()):
        return DoctorCheck(
            name="placement_runtime",
            status="pass",
            message=f"No Kubernetes placement is configured for {kind}.",
        )
    if "kubernetes" not in runtime_modes:
        return DoctorCheck(
            name="placement_runtime",
            status="warn",
            message="Placement is configured but only Kubernetes runtime diagnostics can apply it.",
            repair_command="goblin-king doctor --runtime kubernetes",
            doc_link="docs/kubernetes-placement-and-federation-roadmap.md",
        )
    return DoctorCheck(
        name="placement_runtime",
        status="pass",
        message=f"Kubernetes placement metadata is configured for {kind}.",
    )


def summarize_items(label: str, items: list[str], *, limit: int = 5) -> str:
    """Return a compact human-readable problem list."""
    visible = items[:limit]
    suffix = f" (+{len(items) - limit} more)" if len(items) > limit else ""
    return f"{label}: {', '.join(visible)}{suffix}."


def ensure_utc(value: datetime) -> datetime:
    """Normalize a persisted datetime to UTC for deterministic readiness checks."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


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
