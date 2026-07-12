"""Typer command line interface for the Phase 1 Goblin King kernel."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4

import typer
from redis import Redis
from redis.exceptions import RedisError

from goblin_king.auth import create_api_token, create_project, create_user
from goblin_king.causal_time import causally_after
from goblin_king.cli_support import (
    DEFAULT_IMAGES_PATH,
    DEFAULT_PROJECT_PATH,
    DEFAULT_REDIS_URL,
    DEFAULT_RESOURCE_POLICIES_PATH,
    RuntimeOption,
)
from goblin_king.cli_support import (
    load_input as _load_input,
)
from goblin_king.cli_support import (
    load_project_default_resources as _load_project_default_resources,
)
from goblin_king.cli_support import (
    load_project_registry as _load_project_registry,
)
from goblin_king.cli_support import (
    load_project_settings as _load_project_settings,
)
from goblin_king.cli_support import (
    load_project_workers as _load_project_workers,
)
from goblin_king.cli_support import (
    load_registry as _load_registry,
)
from goblin_king.cli_support import (
    load_resource_policies as _load_resource_policies,
)
from goblin_king.cli_support import (
    load_scheduler_discovery as _load_scheduler_discovery,
)
from goblin_king.cli_support import (
    load_workers as _load_workers,
)
from goblin_king.cli_support import (
    print_validation_results as _print_validation_results,
)
from goblin_king.contracts import (
    DeploymentRecord,
    GoblinDefinition,
    ImagePromotionRecord,
    JobRecord,
    RunRecord,
    ScheduleRecord,
    utc_now,
)
from goblin_king.demo import (
    DEFAULT_ADMIN_TOKEN,
    DEFAULT_ADMIN_URL,
    DEFAULT_DEMO_INPUT,
    DEFAULT_DEMO_KIND,
    DEFAULT_DEMO_PROJECT,
    DEFAULT_POLL_SECONDS,
    DEFAULT_TIMEOUT_SECONDS,
    run_demo_down,
    run_demo_up,
)
from goblin_king.deployment import helm_template_command, image_push_command, run_command
from goblin_king.doctor import DoctorRuntimeSelection, run_doctor
from goblin_king.events import (
    DEFAULT_EVENT_CHANNEL,
    DEFAULT_EVENT_STREAM,
    DEFAULT_EVENT_STREAM_GROUP,
    read_stream_group,
    stream_status,
)
from goblin_king.fanout import (
    FanoutCreateRequest,
    RetryCreateRequest,
    create_fanout,
    fanout_detail,
    list_fanout_details,
    retry_job,
)
from goblin_king.jsonio import pretty_json
from goblin_king.kubernetes_cli import (
    KubernetesRuntimeSettingsPathOption,
    ResultForwarderImageOption,
    ResultForwarderImagePullPolicyOption,
    WorkerImagePullPolicyOption,
    WorkloadImagePullSecretsOption,
    kubernetes_runtime_settings,
)
from goblin_king.kubernetes_runtime_factory import build_kubernetes_runtime
from goblin_king.kubernetes_runtime_settings import (
    DEFAULT_KUBERNETES_IMAGE_PULL_POLICY,
    DEFAULT_RESULT_FORWARDER_IMAGE,
    KubernetesRuntimeSettings,
)
from goblin_king.kubernetes_validation import validate_workers_with_kubernetes
from goblin_king.metadata import goblin_job_metadata
from goblin_king.registry import GoblinRegistry, RegistryError
from goblin_king.resource_policies import ResourcePolicyError, ResourcePolicySet
from goblin_king.runtime import DockerRuntime, InProcessRuntime, new_run_context
from goblin_king.runtime_helpers import docker_policy_args, kubernetes_policy_fields
from goblin_king.scheduler import DEFAULT_INTERVAL_SECONDS, Scheduler, next_run_after
from goblin_king.smoke import run_adopter_project_smoke
from goblin_king.store import DEFAULT_DB_PATH, SQLiteStore
from goblin_king.templates import (
    TemplateError,
    init_package,
    init_project,
    list_project_templates,
)
from goblin_king.validation import validate_workers, validation_record
from goblin_king.workers import WorkerConfigError, WorkerImageDefinition, WorkerImageMap

app = typer.Typer(help="Run and inspect Goblin King jobs.")
api_app = typer.Typer(help="Run the HTTP API control plane.")
auth_app = typer.Typer(help="Manage local API users, projects, and tokens.")
goblins_app = typer.Typer(help="Inspect registered goblins.")
jobs_app = typer.Typer(help="Submit goblin jobs.")
fanouts_app = typer.Typer(help="Inspect fanout batches.")
events_app = typer.Typer(help="Inspect and watch durable events.")
heartbeats_app = typer.Typer(help="Inspect scheduler and worker heartbeats.")
deploy_app = typer.Typer(help="Record image promotion and deployment proof.")
deploy_promotions_app = typer.Typer(help="Plan and inspect worker image promotions.")
demo_app = typer.Typer(help="Run the local Docker admin onboarding demo.")
project_app = typer.Typer(help="Inspect and scaffold reusable Goblin King projects.")
project_goblins_app = typer.Typer(help="Inspect project-discovered goblins.")
project_templates_app = typer.Typer(help="List scaffold project templates.")
runs_app = typer.Typer(help="Inspect goblin runs.")
schedules_app = typer.Typer(help="Create and inspect schedules.")
scheduler_app = typer.Typer(help="Run scheduler passes.")
smoke_app = typer.Typer(help="Run local end-to-end smoke proofs.")
workers_app = typer.Typer(help="Build and validate container worker images.")
resource_policies_app = typer.Typer(help="Inspect runtime resource policy mappings.")
directory_ui_app = typer.Typer(help="Run the Goblin Directory browser service.")
DockerRunRootOption = Annotated[
    Path | None,
    typer.Option(
        "--run-root",
        help="Writable host path shared with the Docker worker data volume.",
    ),
]
ValidationRuntimeOption = Literal["docker", "kubernetes"]
app.add_typer(api_app, name="api")
app.add_typer(auth_app, name="auth")
app.add_typer(goblins_app, name="goblins")
app.add_typer(jobs_app, name="jobs")
app.add_typer(fanouts_app, name="fanouts")
app.add_typer(events_app, name="events")
app.add_typer(heartbeats_app, name="heartbeats")
deploy_app.add_typer(deploy_promotions_app, name="promotions")
app.add_typer(deploy_app, name="deploy")
app.add_typer(demo_app, name="demo")
project_app.add_typer(project_goblins_app, name="goblins")
project_app.add_typer(project_templates_app, name="templates")
app.add_typer(project_app, name="project")
app.add_typer(runs_app, name="runs")
app.add_typer(schedules_app, name="schedules")
app.add_typer(scheduler_app, name="scheduler")
app.add_typer(smoke_app, name="smoke")
app.add_typer(workers_app, name="workers")
app.add_typer(resource_policies_app, name="resource-policies")
app.add_typer(directory_ui_app, name="directory-ui")

@auth_app.command("create-user")
def create_auth_user(
    email: Annotated[str, typer.Option("--email", help="User email.")],
    display_name: Annotated[str, typer.Option("--display-name", help="Display name.")],
    db: Annotated[Path, typer.Option("--db", help="SQLite database path.")] = DEFAULT_DB_PATH,
) -> None:
    """Create a local API user."""
    user = create_user(SQLiteStore(db), email=email, display_name=display_name)
    typer.echo(user.model_dump_json(indent=2))


@auth_app.command("create-project")
def create_auth_project(
    name: Annotated[str, typer.Option("--name", help="Project name.")],
    db: Annotated[Path, typer.Option("--db", help="SQLite database path.")] = DEFAULT_DB_PATH,
) -> None:
    """Create a local project boundary."""
    project = create_project(SQLiteStore(db), name=name)
    typer.echo(project.model_dump_json(indent=2))


@auth_app.command("create-token")
def create_auth_token(
    name: Annotated[str, typer.Option("--name", help="Token name.")],
    user_id: Annotated[str, typer.Option("--user-id", help="User ID.")],
    project_id: Annotated[
        str | None,
        typer.Option("--project-id", help="Optional project scope."),
    ] = None,
    role: Annotated[str, typer.Option("--role", help="Token role.")] = "member",
    db: Annotated[Path, typer.Option("--db", help="SQLite database path.")] = DEFAULT_DB_PATH,
) -> None:
    """Create a local API token and print the raw token once."""
    token, raw = create_api_token(
        SQLiteStore(db),
        name=name,
        user_id=user_id,
        project_id=project_id,
        role=role,
    )
    typer.echo(pretty_json({"token": token.model_dump(mode="json"), "raw_token": raw}))


@api_app.command("run")
def run_api(
    settings: Annotated[
        Path,
        typer.Option("--settings", help="API settings JSON path."),
    ] = Path("goblin-king-api.json"),
    host: Annotated[str, typer.Option("--host", help="API bind host.")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", help="API bind port.")] = 8000,
) -> None:
    """Run the FastAPI control plane with Uvicorn."""
    import uvicorn

    from goblin_king.api import create_app
    from goblin_king.api_settings import ApiSettings, ApiSettingsError

    try:
        loaded_settings = ApiSettings.from_path(settings)
    except ApiSettingsError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from error
    uvicorn.run(create_app(loaded_settings), host=host, port=port)


@directory_ui_app.command("run")
def run_directory_ui_service(
    host: Annotated[
        str,
        typer.Option("--host", help="Directory UI service bind host."),
    ] = "127.0.0.1",
    port: Annotated[
        int,
        typer.Option("--port", help="Directory UI service bind port."),
    ] = 8080,
) -> None:
    """Run the JupyterHub-authenticated directory browser service."""
    from goblin_king.directory_ui import DirectoryUISettings, run_directory_ui

    run_directory_ui(
        host=host,
        port=port,
        settings=DirectoryUISettings.from_env(),
    )


@demo_app.command("up")
def demo_up(
    project: Annotated[
        Path,
        typer.Option("--project", help="Goblin King project settings path for the demo."),
    ] = DEFAULT_DEMO_PROJECT,
    kind: Annotated[
        str,
        typer.Option("--kind", help="Project goblin kind to validate and submit."),
    ] = DEFAULT_DEMO_KIND,
    input_path: Annotated[
        Path,
        typer.Option("--input", help="JSON input payload for the demo goblin."),
    ] = DEFAULT_DEMO_INPUT,
    admin_url: Annotated[
        str,
        typer.Option("--admin-url", help="Local React admin URL."),
    ] = DEFAULT_ADMIN_URL,
    token: Annotated[
        str,
        typer.Option("--token", help="Local admin/API bearer token."),
    ] = DEFAULT_ADMIN_TOKEN,
    redis_url: Annotated[
        str,
        typer.Option("--redis-url", help="Redis URL used by validation and Docker runtime."),
    ] = DEFAULT_REDIS_URL,
    timeout_seconds: Annotated[
        int,
        typer.Option("--timeout-seconds", help="Seconds to wait for API and run proof."),
    ] = DEFAULT_TIMEOUT_SECONDS,
    poll_seconds: Annotated[
        float,
        typer.Option("--poll-seconds", help="Seconds between job/run polling attempts."),
    ] = DEFAULT_POLL_SECONDS,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print machine-readable demo proof."),
    ] = False,
) -> None:
    """Start the local admin stack and prove one validated project goblin run."""
    result = run_demo_up(
        project=project,
        kind=kind,
        input_path=input_path,
        admin_url=admin_url,
        token=token,
        redis_url=redis_url,
        timeout_seconds=timeout_seconds,
        poll_seconds=poll_seconds,
    )
    if json_output:
        typer.echo(result.model_dump_json(indent=2))
    else:
        _print_demo_up_result(result)
    if not result.ok:
        raise typer.Exit(1)


@demo_app.command("down")
def demo_down(
    project: Annotated[
        Path,
        typer.Option("--project", help="Goblin King project settings path used for Compose env."),
    ] = DEFAULT_DEMO_PROJECT,
) -> None:
    """Stop the local Docker Compose demo stack."""
    result = run_demo_down(project=project)
    typer.echo(f"cleanup\t{result.cleanup}")
    typer.echo(f"compose\t{'ok' if result.ok else 'failed'}")
    if result.error:
        typer.echo(result.error, err=True)
    if not result.ok:
        raise typer.Exit(1)


@app.command("doctor")
def doctor(
    project: Annotated[
        Path,
        typer.Option("--project", help="Goblin King project settings path to diagnose."),
    ] = DEFAULT_DEMO_PROJECT,
    kind: Annotated[
        str,
        typer.Option("--kind", help="Project goblin kind whose validation status is checked."),
    ] = DEFAULT_DEMO_KIND,
    admin_url: Annotated[
        str,
        typer.Option("--admin-url", help="Local React admin URL."),
    ] = DEFAULT_ADMIN_URL,
    token: Annotated[
        str,
        typer.Option("--token", help="Local admin/API bearer token."),
    ] = DEFAULT_ADMIN_TOKEN,
    redis_url: Annotated[
        str,
        typer.Option("--redis-url", help="Redis URL checked by diagnostics."),
    ] = DEFAULT_REDIS_URL,
    runtime: Annotated[
        DoctorRuntimeSelection,
        typer.Option("--runtime", help="Runtime diagnostics to include."),
    ] = "docker",
    resource_policies: Annotated[
        Path | None,
        typer.Option("--resource-policies", help="Optional runtime resource policy JSON path."),
    ] = None,
    helm_values: Annotated[
        Path | None,
        typer.Option("--helm-values", help="Optional Helm values file for a local render check."),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print machine-readable diagnostic checks."),
    ] = False,
) -> None:
    """Diagnose local prerequisites for the demo/adopter onboarding path."""
    result = run_doctor(
        project=project,
        kind=kind,
        admin_url=admin_url,
        token=token,
        redis_url=redis_url,
        runtime=runtime,
        resource_policies=resource_policies,
        helm_values=helm_values,
    )
    if json_output:
        typer.echo(result.model_dump_json(indent=2))
    else:
        for check in result.checks:
            typer.echo(f"{check.status}\t{check.name}\t{check.message}")
            if check.repair_command:
                typer.echo(f"repair\t{check.repair_command}")
            if check.doc_link:
                typer.echo(f"docs\t{check.doc_link}")
    if not result.ok:
        raise typer.Exit(1)


@goblins_app.command("list")
def list_goblins(
    registry: Annotated[
        Path,
        typer.Option("--registry", help="Registry JSON path."),
    ] = Path("goblins.json"),
) -> None:
    """Print registered goblin kinds and display names."""
    loaded = _load_registry(registry)
    for definition in loaded.list():
        typer.echo(f"{definition.kind}\t{definition.display_name}")


@project_goblins_app.command("list")
def list_project_goblins(
    project: Annotated[
        Path,
        typer.Option("--project", help="Goblin King project settings path."),
    ] = DEFAULT_PROJECT_PATH,
) -> None:
    """Print goblins discovered from a project settings file."""
    loaded = _load_project_registry(project)
    for definition in loaded.list():
        typer.echo(f"{definition.kind}\t{definition.display_name}")


@project_templates_app.command("list")
def list_project_template_profiles(
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print machine-readable project template metadata."),
    ] = False,
) -> None:
    """Print available project template profiles."""
    profiles = list_project_templates()
    if json_output:
        typer.echo(
            pretty_json(
                [
                    {"name": profile.name, "description": profile.description}
                    for profile in profiles
                ]
            )
        )
        return
    for profile in profiles:
        typer.echo(f"{profile.name}\t{profile.description}")


@project_app.command("validate")
def validate_project(
    project: Annotated[
        Path,
        typer.Option("--project", help="Goblin King project settings path."),
    ] = DEFAULT_PROJECT_PATH,
    check_worker_builds: Annotated[
        bool,
        typer.Option(
            "--check-worker-builds",
            help="Run docker build for every configured worker after static validation.",
        ),
    ] = False,
) -> None:
    """Validate project settings, registry discovery, and worker image settings."""
    settings = _load_project_settings(project)
    registry = _load_project_registry(project)
    workers = _load_project_workers(settings)
    missing: list[str] = []
    invalid: list[str] = []
    for definition in registry.list():
        try:
            worker = workers.get(definition.kind)
        except WorkerConfigError:
            missing.append(definition.kind)
            continue
        context = workers.resolved_context(worker)
        dockerfile = context / worker.dockerfile
        if not dockerfile.exists():
            invalid.append(f"{definition.kind}\t{dockerfile}")

    if missing or invalid:
        for kind in missing:
            typer.echo(f"missing_worker\t{kind}", err=True)
        for item in invalid:
            typer.echo(f"missing_dockerfile\t{item}", err=True)
        raise typer.Exit(1)

    if check_worker_builds:
        runtime = DockerRuntime(workers=workers)
        for definition in registry.list():
            runtime.build_image(definition.kind)

    typer.echo(f"registries\t{len(settings.registries)}")
    typer.echo(f"entry_points\t{settings.entry_points}")
    typer.echo(f"goblins\t{len(registry.list())}")
    typer.echo(f"services\t{len(settings.services)}")
    typer.echo(f"workers\t{len(workers.items())}")
    typer.echo(f"worker_coverage\t{len(registry.list())}/{len(registry.list())}")
    default_resources = _load_project_default_resources(project)
    if default_resources:
        typer.echo(f"defaults.resources\t{json.dumps(default_resources, sort_keys=True)}")
    typer.echo("dockerfiles\tok")


@project_app.command("init-package")
def init_project_package(
    target_dir: Annotated[Path, typer.Argument(help="Directory to create.")],
    kind: Annotated[str, typer.Option("--kind", help="Generated goblin kind.")],
    package_name: Annotated[
        str,
        typer.Option("--package-name", help="Generated Python package name."),
    ],
    image: Annotated[str, typer.Option("--image", help="Generated worker image tag.")],
    include_long_service: Annotated[
        bool,
        typer.Option(
            "--long-service/--no-long-service",
            help="Include a generated long-running service worker folder.",
        ),
    ] = True,
) -> None:
    """Generate a reusable goblin package and self-contained worker folder."""
    try:
        created = init_package(
            target_dir,
            kind=kind,
            package_name=package_name,
            image=image,
            include_long_service=include_long_service,
        )
    except TemplateError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from error
    typer.echo(f"created {created}")


@project_app.command("init")
def init_project_template(
    target_dir: Annotated[Path, typer.Argument(help="Directory to create.")],
    prefix: Annotated[
        str,
        typer.Option("--prefix", help="Kind and image prefix for generated goblins."),
    ] = "project",
    profile: Annotated[
        str,
        typer.Option("--profile", help="Generated project template profile."),
    ] = "basic",
) -> None:
    """Generate a standalone adopter project with contract-compliant workers."""
    try:
        created = init_project(target_dir, prefix=prefix, profile=profile)
    except TemplateError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from error
    typer.echo(f"created {created}")


@smoke_app.command("adopter-project")
def smoke_adopter_project(
    work_dir: Annotated[
        Path | None,
        typer.Option("--work-dir", help="Optional directory for generated smoke files."),
    ] = None,
    prefix: Annotated[
        str,
        typer.Option("--prefix", help="Generated goblin kind prefix."),
    ] = "smoke",
    redis_url: Annotated[
        str,
        typer.Option("--redis-url", help="Redis URL used by Docker runtime."),
    ] = DEFAULT_REDIS_URL,
    keep: Annotated[
        bool,
        typer.Option("--keep", help="Keep generated smoke project files."),
    ] = False,
) -> None:
    """Run the local adopter-project smoke suite through Docker."""
    result = run_adopter_project_smoke(
        work_dir=work_dir,
        prefix=prefix,
        redis_url=redis_url,
        keep=keep,
    )
    typer.echo(result.model_dump_json(indent=2))
    if not result.ok:
        raise typer.Exit(1)


@jobs_app.command("submit")
def submit_job(
    kind: Annotated[str, typer.Argument(help="Goblin kind to execute.")],
    input_path: Annotated[Path, typer.Option("--input", help="JSON input payload path.")],
    registry: Annotated[
        Path,
        typer.Option("--registry", help="Registry JSON path."),
    ] = Path("goblins.json"),
    project: Annotated[
        Path | None,
        typer.Option("--project", help="Optional project settings path."),
    ] = None,
    db: Annotated[Path, typer.Option("--db", help="SQLite database path.")] = DEFAULT_DB_PATH,
    runtime: Annotated[
        RuntimeOption,
        typer.Option("--runtime", help="Execution runtime."),
    ] = "docker",
    images: Annotated[
        Path,
        typer.Option("--images", help="Worker image map path for Docker runtime."),
    ] = DEFAULT_IMAGES_PATH,
    redis_url: Annotated[
        str,
        typer.Option("--redis-url", help="Redis URL used by Docker result transport."),
    ] = DEFAULT_REDIS_URL,
    run_root: DockerRunRootOption = None,
    resource_policies: Annotated[
        Path | None,
        typer.Option("--resource-policies", help="Optional resource policy JSON path."),
    ] = DEFAULT_RESOURCE_POLICIES_PATH,
    result_forwarder_image: ResultForwarderImageOption = DEFAULT_RESULT_FORWARDER_IMAGE,
    worker_image_pull_policy: WorkerImagePullPolicyOption = (
        DEFAULT_KUBERNETES_IMAGE_PULL_POLICY
    ),
    result_forwarder_image_pull_policy: ResultForwarderImagePullPolicyOption = (
        DEFAULT_KUBERNETES_IMAGE_PULL_POLICY
    ),
    workload_image_pull_secrets: WorkloadImagePullSecretsOption = None,
    kubernetes_runtime_settings_path: KubernetesRuntimeSettingsPathOption = None,
) -> None:
    """Submit and immediately execute one job through the selected runtime."""
    loaded, worker_map = _load_scheduler_discovery(registry, images, project, runtime)
    input_payload = _load_input(input_path)
    try:
        definition = loaded.get(kind)
        entrypoint = loaded.resolve(kind)[1] if runtime == "in-process" else None
    except (RegistryError, WorkerConfigError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from error
    store = SQLiteStore(db)
    policy_set = _load_resource_policies(resource_policies, project=project)
    try:
        policy = (
            policy_set.effective_for(
                definition.kind,
                timeout_seconds=definition.timeout_seconds,
                max_retries=definition.max_retries,
            )
            if policy_set
            else None
        )
    except ResourcePolicyError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from error

    job = JobRecord(
        id=str(uuid4()),
        kind=definition.kind,
        input=input_payload,
        created_at=utc_now(),
        timeout_seconds=policy.timeout_seconds if policy else definition.timeout_seconds,
        max_retries=(policy.max_retries or 0) if policy else (definition.max_retries or 0),
        metadata=goblin_job_metadata(definition, policy),
    )
    store.save_job(job)
    context = new_run_context(job.id, definition.kind)
    if policy is not None:
        context = context.model_copy(
            update={
                "metadata": {**context.metadata, "resource_policy": policy.compact()}
            }
        )
    started_at = utc_now()
    if runtime == "docker":
        result = DockerRuntime(
            workers=worker_map,
            redis_url=redis_url,
            run_root=run_root,
        ).run(
            definition,
            entrypoint,
            input_payload,
            context,
            timeout_seconds=job.timeout_seconds,
            resource_policy=policy,
        )
    elif runtime == "kubernetes":
        result = build_kubernetes_runtime(
            workers=worker_map,
            redis_url=redis_url,
            event_bus=None,
            settings=kubernetes_runtime_settings(
                result_forwarder_image=result_forwarder_image,
                worker_image_pull_policy=worker_image_pull_policy,
                result_forwarder_image_pull_policy=result_forwarder_image_pull_policy,
                workload_image_pull_secrets=workload_image_pull_secrets,
                settings_path=kubernetes_runtime_settings_path,
            ),
        ).run(
            definition,
            entrypoint,
            input_payload,
            context,
            timeout_seconds=job.timeout_seconds,
            resource_policy=policy,
        )
    else:
        result = InProcessRuntime().run(definition, entrypoint, input_payload, context)
    finished_at = causally_after(started_at, candidate=utc_now())
    run = RunRecord(
        id=context.run_id,
        job_id=job.id,
        kind=definition.kind,
        status="completed" if result.status == "success" else "failed",
        started_at=started_at,
        finished_at=finished_at,
        result=result,
        error=result.error,
        timeout_seconds=job.timeout_seconds,
        max_retries=job.max_retries,
        resource_policy=policy.compact() if policy else None,
    )
    store.save_run(run)
    store.finish_job(job.id, status=run.status, last_error=run.error)

    typer.echo(run.model_dump_json(indent=2))
    if run.status == "failed":
        raise typer.Exit(1)


@jobs_app.command("fanout")
def fanout_jobs(
    input_path: Annotated[Path, typer.Option("--input", help="Fanout JSON request path.")],
    registry: Annotated[
        Path,
        typer.Option("--registry", help="Registry JSON path."),
    ] = Path("goblins.json"),
    db: Annotated[Path, typer.Option("--db", help="SQLite database path.")] = DEFAULT_DB_PATH,
    resource_policies: Annotated[
        Path | None,
        typer.Option("--resource-policies", help="Optional resource policy JSON path."),
    ] = DEFAULT_RESOURCE_POLICIES_PATH,
) -> None:
    """Create a queued fanout batch from a JSON request."""
    request = FanoutCreateRequest.model_validate(_load_input(input_path))
    detail = create_fanout(
        store=SQLiteStore(db),
        registry=_load_registry(registry),
        request=request,
        created_by="cli",
        resource_policies=_load_resource_policies(resource_policies),
    )
    typer.echo(detail.model_dump_json(indent=2))


@jobs_app.command("retry")
def retry_cli_job(
    job_id: Annotated[str, typer.Argument(help="Source job ID to retry.")],
    db: Annotated[Path, typer.Option("--db", help="SQLite database path.")] = DEFAULT_DB_PATH,
    reason: Annotated[str | None, typer.Option("--reason", help="Retry reason.")] = None,
    resource_policies: Annotated[
        Path | None,
        typer.Option("--resource-policies", help="Optional resource policy JSON path."),
    ] = DEFAULT_RESOURCE_POLICIES_PATH,
) -> None:
    """Create a queued retry job from a terminal source job."""
    try:
        retry = retry_job(
            store=SQLiteStore(db),
            job_id=job_id,
            request=RetryCreateRequest(reason=reason),
            created_by="cli-retry",
            resource_policies=_load_resource_policies(resource_policies),
        )
    except KeyError as error:
        typer.echo(f"job not found: {job_id}", err=True)
        raise typer.Exit(1) from error
    except ValueError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from error
    typer.echo(retry.model_dump_json(indent=2))


@jobs_app.command("list")
def list_jobs(
    db: Annotated[Path, typer.Option("--db", help="SQLite database path.")] = DEFAULT_DB_PATH,
) -> None:
    """Print persisted jobs in creation order."""
    store = SQLiteStore(db)
    for job in store.list_jobs():
        typer.echo(f"{job.id}\t{job.kind}\t{job.status}\t{job.due_at or ''}")


@fanouts_app.command("list")
def list_fanouts(
    db: Annotated[Path, typer.Option("--db", help="SQLite database path.")] = DEFAULT_DB_PATH,
) -> None:
    """Print persisted fanout batches with derived status."""
    for detail in list_fanout_details(SQLiteStore(db)):
        typer.echo(
            f"{detail.fanout.id}\t{detail.status}\t{detail.counts.get('total', 0)}"
            f"\t{detail.fanout.description or ''}"
        )


@fanouts_app.command("show")
def show_fanout(
    fanout_id: Annotated[str, typer.Argument(help="Fanout ID to inspect.")],
    db: Annotated[Path, typer.Option("--db", help="SQLite database path.")] = DEFAULT_DB_PATH,
) -> None:
    """Print one fanout batch as JSON."""
    try:
        detail = fanout_detail(SQLiteStore(db), fanout_id)
    except KeyError as error:
        typer.echo(f"fanout not found: {fanout_id}", err=True)
        raise typer.Exit(1) from error
    typer.echo(detail.model_dump_json(indent=2))


@events_app.command("list")
def list_events(
    db: Annotated[Path, typer.Option("--db", help="SQLite database path.")] = DEFAULT_DB_PATH,
    limit: Annotated[int, typer.Option("--limit", help="Maximum events to print.")] = 100,
) -> None:
    """Print durable events as JSON lines."""
    store = SQLiteStore(db)
    for event in store.list_events(limit=limit):
        typer.echo(event.model_dump_json())


@events_app.command("watch")
def watch_events(
    redis_url: Annotated[
        str,
        typer.Option("--redis-url", help="Redis URL used by event pub/sub."),
    ] = DEFAULT_REDIS_URL,
    channel: Annotated[
        str,
        typer.Option("--channel", help="Redis event channel."),
    ] = DEFAULT_EVENT_CHANNEL,
    limit: Annotated[
        int | None,
        typer.Option("--limit", help="Stop after this many events."),
    ] = None,
) -> None:
    """Watch live event envelopes from Redis pub/sub."""
    seen = 0
    try:
        pubsub = Redis.from_url(redis_url).pubsub()
        pubsub.subscribe(channel)
        for message in pubsub.listen():
            if message.get("type") != "message":
                continue
            data = message.get("data")
            typer.echo(data.decode("utf-8") if isinstance(data, bytes) else str(data))
            seen += 1
            if limit is not None and seen >= limit:
                break
    except RedisError as error:
        typer.echo(f"redis pubsub failed: {error}", err=True)
        raise typer.Exit(1) from error
    finally:
        try:
            pubsub.close()
        except UnboundLocalError:
            pass


@events_app.command("stream-status")
def event_stream_status(
    redis_url: Annotated[
        str,
        typer.Option("--redis-url", help="Redis URL used by event streams."),
    ] = DEFAULT_REDIS_URL,
    stream: Annotated[
        str,
        typer.Option("--stream", help="Redis Stream key to inspect."),
    ] = DEFAULT_EVENT_STREAM,
) -> None:
    """Print Redis Stream health and consumer lag metadata."""
    typer.echo(pretty_json(stream_status(redis_url, stream=stream)))


@events_app.command("stream-read")
def event_stream_read(
    redis_url: Annotated[
        str,
        typer.Option("--redis-url", help="Redis URL used by event streams."),
    ] = DEFAULT_REDIS_URL,
    stream: Annotated[
        str,
        typer.Option("--stream", help="Redis Stream key to read."),
    ] = DEFAULT_EVENT_STREAM,
    group: Annotated[
        str,
        typer.Option("--group", help="Redis Stream consumer group."),
    ] = DEFAULT_EVENT_STREAM_GROUP,
    consumer: Annotated[
        str,
        typer.Option("--consumer", help="Consumer name for this read."),
    ] = "goblin-king-cli",
    limit: Annotated[int, typer.Option("--limit", help="Maximum events to read.")] = 10,
    ack: Annotated[
        bool,
        typer.Option("--ack", help="Acknowledge messages after printing them."),
    ] = False,
) -> None:
    """Read event envelopes from Redis Streams through a consumer group."""
    try:
        for event in read_stream_group(
            redis_url,
            stream=stream,
            group=group,
            consumer=consumer,
            count=limit,
            ack=ack,
        ):
            typer.echo(json.dumps(event))
    except RedisError as error:
        typer.echo(f"redis stream read failed: {error}", err=True)
        raise typer.Exit(1) from error


@heartbeats_app.command("list")
def list_heartbeats(
    db: Annotated[Path, typer.Option("--db", help="SQLite database path.")] = DEFAULT_DB_PATH,
) -> None:
    """Print scheduler and worker heartbeat records."""
    store = SQLiteStore(db)
    for heartbeat in store.list_heartbeats():
        typer.echo(heartbeat.model_dump_json())


@runs_app.command("show")
def show_run(
    run_id: Annotated[str, typer.Argument(help="Run ID to inspect.")],
    db: Annotated[Path, typer.Option("--db", help="SQLite database path.")] = DEFAULT_DB_PATH,
    with_job: Annotated[
        bool,
        typer.Option("--with-job", help="Include source job metadata in the output."),
    ] = False,
) -> None:
    """Print a persisted run record as JSON."""
    store = SQLiteStore(db)
    run = store.get_run(run_id)
    if run is None:
        typer.echo(f"run not found: {run_id}", err=True)
        raise typer.Exit(1)
    if not with_job:
        typer.echo(run.model_dump_json(indent=2))
        return
    job = store.get_job(run.job_id)
    typer.echo(
        json.dumps(
            {
                "run": run.model_dump(mode="json"),
                "job": job.model_dump(mode="json") if job else None,
                "goblin_source": (job.metadata.get("goblin_source") if job else None),
                "goblin_definition": (
                    job.metadata.get("goblin_definition") if job else None
                ),
            },
            indent=2,
        )
    )


@schedules_app.command("add")
def add_schedule(
    kind: Annotated[str, typer.Argument(help="Goblin kind to schedule.")],
    cron: Annotated[str, typer.Option("--cron", help="Cron expression.")],
    input_path: Annotated[Path, typer.Option("--input", help="JSON input payload path.")],
    registry: Annotated[
        Path,
        typer.Option("--registry", help="Registry JSON path."),
    ] = Path("goblins.json"),
    project: Annotated[
        Path | None,
        typer.Option("--project", help="Optional project settings path."),
    ] = None,
    db: Annotated[Path, typer.Option("--db", help="SQLite database path.")] = DEFAULT_DB_PATH,
    due_now: Annotated[
        bool,
        typer.Option("--due-now", help="Make the schedule due immediately for local smoke tests."),
    ] = False,
    timezone: Annotated[str, typer.Option("--timezone", help="Schedule timezone.")] = "UTC",
    max_retries: Annotated[int, typer.Option("--max-retries", help="Maximum retry attempts.")] = 0,
    timeout_seconds: Annotated[
        int | None,
        typer.Option("--timeout-seconds", help="Optional timeout in seconds."),
    ] = None,
    resource_policies: Annotated[
        Path | None,
        typer.Option("--resource-policies", help="Optional resource policy JSON path."),
    ] = DEFAULT_RESOURCE_POLICIES_PATH,
) -> None:
    """Persist a recurring goblin schedule."""
    loaded = _load_project_registry(project) if project is not None else _load_registry(registry)
    try:
        definition = loaded.get(kind)
    except RegistryError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from error
    input_payload = _load_input(input_path)
    policy_set = _load_resource_policies(resource_policies, project=project)
    try:
        policy = (
            policy_set.effective_for(
                definition.kind,
                timeout_seconds=timeout_seconds,
                max_retries=max_retries,
            )
            if policy_set
            else None
        )
    except ResourcePolicyError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from error
    created_at = utc_now()
    provisional = ScheduleRecord(
        id=str(uuid4()),
        kind=definition.kind,
        input=input_payload,
        cron=cron,
        timezone=timezone,
        created_at=created_at,
        next_run_at=created_at,
        max_retries=(policy.max_retries or 0) if policy else max_retries,
        timeout_seconds=policy.timeout_seconds if policy else timeout_seconds,
    )
    schedule = provisional.model_copy(
        update={
            "next_run_at": created_at if due_now else next_run_after(provisional, created_at),
        }
    )
    store = SQLiteStore(db)
    store.save_schedule(schedule)
    typer.echo(schedule.model_dump_json(indent=2))


@schedules_app.command("list")
def list_schedules(
    db: Annotated[Path, typer.Option("--db", help="SQLite database path.")] = DEFAULT_DB_PATH,
) -> None:
    """Print persisted schedules ordered by next run time."""
    store = SQLiteStore(db)
    for schedule in store.list_schedules():
        enabled = "enabled" if schedule.enabled else "disabled"
        typer.echo(f"{schedule.id}\t{schedule.kind}\t{enabled}\t{schedule.next_run_at}")


@scheduler_app.command("run-once")
def scheduler_run_once(
    registry: Annotated[
        Path,
        typer.Option("--registry", help="Registry JSON path."),
    ] = Path("goblins.json"),
    project: Annotated[
        Path | None,
        typer.Option("--project", help="Optional project settings path."),
    ] = None,
    db: Annotated[Path, typer.Option("--db", help="SQLite database path.")] = DEFAULT_DB_PATH,
    runtime: Annotated[
        RuntimeOption,
        typer.Option("--runtime", help="Execution runtime."),
    ] = "docker",
    images: Annotated[
        Path,
        typer.Option("--images", help="Worker image map path for Docker runtime."),
    ] = DEFAULT_IMAGES_PATH,
    redis_url: Annotated[
        str,
        typer.Option("--redis-url", help="Redis URL used by Docker result transport."),
    ] = DEFAULT_REDIS_URL,
    run_root: DockerRunRootOption = None,
    resource_policies: Annotated[
        Path | None,
        typer.Option("--resource-policies", help="Optional resource policy JSON path."),
    ] = DEFAULT_RESOURCE_POLICIES_PATH,
    result_forwarder_image: ResultForwarderImageOption = DEFAULT_RESULT_FORWARDER_IMAGE,
    worker_image_pull_policy: WorkerImagePullPolicyOption = (
        DEFAULT_KUBERNETES_IMAGE_PULL_POLICY
    ),
    result_forwarder_image_pull_policy: ResultForwarderImagePullPolicyOption = (
        DEFAULT_KUBERNETES_IMAGE_PULL_POLICY
    ),
    workload_image_pull_secrets: WorkloadImagePullSecretsOption = None,
    kubernetes_runtime_settings_path: KubernetesRuntimeSettingsPathOption = None,
) -> None:
    """Run one deterministic scheduler pass and print any created runs."""
    registry, workers = _load_scheduler_discovery(registry, images, project, runtime)
    scheduler = Scheduler(
        registry=registry,
        store=SQLiteStore(db),
        runtime_mode=runtime,
        workers=workers,
        redis_url=redis_url,
        docker_run_root=run_root,
        kubernetes_runtime_settings=kubernetes_runtime_settings(
            result_forwarder_image=result_forwarder_image,
            worker_image_pull_policy=worker_image_pull_policy,
            result_forwarder_image_pull_policy=result_forwarder_image_pull_policy,
            workload_image_pull_secrets=workload_image_pull_secrets,
            settings_path=kubernetes_runtime_settings_path,
        ),
        resource_policies=_load_resource_policies(resource_policies, project=project),
    )
    runs = scheduler.run_once()
    typer.echo(pretty_json([run.model_dump(mode="json") for run in runs]))


@scheduler_app.command("run")
def scheduler_run(
    registry: Annotated[
        Path,
        typer.Option("--registry", help="Registry JSON path."),
    ] = Path("goblins.json"),
    project: Annotated[
        Path | None,
        typer.Option("--project", help="Optional project settings path."),
    ] = None,
    db: Annotated[Path, typer.Option("--db", help="SQLite database path.")] = DEFAULT_DB_PATH,
    interval_seconds: Annotated[
        int,
        typer.Option("--interval-seconds", help="Seconds between scheduler passes."),
    ] = DEFAULT_INTERVAL_SECONDS,
    runtime: Annotated[
        RuntimeOption,
        typer.Option("--runtime", help="Execution runtime."),
    ] = "docker",
    images: Annotated[
        Path,
        typer.Option("--images", help="Worker image map path for Docker runtime."),
    ] = DEFAULT_IMAGES_PATH,
    redis_url: Annotated[
        str,
        typer.Option("--redis-url", help="Redis URL used by Docker result transport."),
    ] = DEFAULT_REDIS_URL,
    run_root: DockerRunRootOption = None,
    resource_policies: Annotated[
        Path | None,
        typer.Option("--resource-policies", help="Optional resource policy JSON path."),
    ] = DEFAULT_RESOURCE_POLICIES_PATH,
    result_forwarder_image: ResultForwarderImageOption = DEFAULT_RESULT_FORWARDER_IMAGE,
    worker_image_pull_policy: WorkerImagePullPolicyOption = (
        DEFAULT_KUBERNETES_IMAGE_PULL_POLICY
    ),
    result_forwarder_image_pull_policy: ResultForwarderImagePullPolicyOption = (
        DEFAULT_KUBERNETES_IMAGE_PULL_POLICY
    ),
    workload_image_pull_secrets: WorkloadImagePullSecretsOption = None,
    kubernetes_runtime_settings_path: KubernetesRuntimeSettingsPathOption = None,
) -> None:
    """Run scheduler passes until interrupted."""
    registry, workers = _load_scheduler_discovery(registry, images, project, runtime)
    scheduler = Scheduler(
        registry=registry,
        store=SQLiteStore(db),
        runtime_mode=runtime,
        workers=workers,
        redis_url=redis_url,
        docker_run_root=run_root,
        kubernetes_runtime_settings=kubernetes_runtime_settings(
            result_forwarder_image=result_forwarder_image,
            worker_image_pull_policy=worker_image_pull_policy,
            result_forwarder_image_pull_policy=result_forwarder_image_pull_policy,
            workload_image_pull_secrets=workload_image_pull_secrets,
            settings_path=kubernetes_runtime_settings_path,
        ),
        resource_policies=_load_resource_policies(resource_policies, project=project),
    )
    try:
        scheduler.run_loop(interval_seconds=interval_seconds)
    except KeyboardInterrupt:
        typer.echo("scheduler stopped")


@deploy_promotions_app.command("plan")
def plan_image_promotion(
    kind: Annotated[str, typer.Argument(help="Goblin kind whose worker image is promoted.")],
    target_image: Annotated[str, typer.Option("--target-image", help="Target promoted image tag.")],
    db: Annotated[Path, typer.Option("--db", help="SQLite database path.")] = DEFAULT_DB_PATH,
    images: Annotated[
        Path,
        typer.Option("--images", help="Worker image map path."),
    ] = DEFAULT_IMAGES_PATH,
    source_image: Annotated[
        str | None,
        typer.Option("--source-image", help="Override source image tag."),
    ] = None,
    build: Annotated[
        bool,
        typer.Option("--build/--no-build", help="Include Docker build command proof."),
    ] = False,
    push: Annotated[
        bool,
        typer.Option("--push/--no-push", help="Include Docker push command proof."),
    ] = False,
) -> None:
    """Record one worker image promotion plan without pushing by default."""
    worker_map = _load_workers(images)
    worker = worker_map.get(kind)
    source = source_image or worker.image
    context = worker_map.resolved_context(worker)
    commands: list[list[str]] = []
    if build:
        commands.append(
            ["docker", "build", "-f", str(context / worker.dockerfile), "-t", source, str(context)]
        )
    if push:
        commands.append(image_push_command(target_image))
    now = utc_now()
    promotion = ImagePromotionRecord(
        id=str(uuid4()),
        kind=kind,
        source_image=source,
        target_image=target_image,
        status="planned",
        actor="cli",
        created_at=now,
        updated_at=now,
        detail={
            "dry_run": True,
            "commands": commands,
            "worker_context": str(context),
            "dockerfile": worker.dockerfile,
        },
    )
    SQLiteStore(db).save_image_promotion(promotion)
    typer.echo(pretty_json(promotion.model_dump(mode="json")))


@deploy_promotions_app.command("list")
def list_image_promotions(
    db: Annotated[Path, typer.Option("--db", help="SQLite database path.")] = DEFAULT_DB_PATH,
) -> None:
    """Print recent worker image promotion records."""
    promotions = SQLiteStore(db).list_image_promotions()
    for promotion in promotions:
        typer.echo(
            f"{promotion.id}\t{promotion.kind}\t{promotion.status}\t"
            f"{promotion.source_image} -> {promotion.target_image}"
        )


@deploy_promotions_app.command("mark")
def mark_image_promotion(
    promotion_id: Annotated[str, typer.Argument(help="Promotion record ID.")],
    status: Annotated[str, typer.Option("--status", help="New promotion status.")] = "promoted",
    digest: Annotated[
        str | None,
        typer.Option("--digest", help="Optional promoted image digest."),
    ] = None,
    db: Annotated[Path, typer.Option("--db", help="SQLite database path.")] = DEFAULT_DB_PATH,
) -> None:
    """Mark an image promotion proof record as built, pushed, promoted, or failed."""
    promotion = SQLiteStore(db).update_image_promotion(
        promotion_id,
        status=status,
        digest=digest,
        detail={"marked_by": "cli"},
        updated_at=utc_now(),
    )
    if promotion is None:
        typer.echo("image promotion not found", err=True)
        raise typer.Exit(1)
    typer.echo(pretty_json(promotion.model_dump(mode="json")))


@deploy_app.command("helm-template")
def record_helm_template(
    db: Annotated[Path, typer.Option("--db", help="SQLite database path.")] = DEFAULT_DB_PATH,
    chart: Annotated[
        Path,
        typer.Option("--chart", help="Helm chart path."),
    ] = Path("charts/goblin-king"),
    release: Annotated[str, typer.Option("--release", help="Helm release name.")] = "goblin-king",
    namespace: Annotated[
        str | None,
        typer.Option("--namespace", help="Optional namespace."),
    ] = None,
    values: Annotated[
        Path | None,
        typer.Option("--values", help="Optional values file."),
    ] = None,
    execute: Annotated[
        bool,
        typer.Option(
            "--execute/--record-only",
            help="Run helm template instead of recording only.",
        ),
    ] = False,
) -> None:
    """Record or execute a Helm template proof command."""
    command = helm_template_command(
        chart=chart,
        release=release,
        namespace=namespace,
        values=values,
    )
    output = None
    status = "planned"
    detail: dict[str, object] = {"execute": execute}
    if execute:
        code, output = run_command(command)
        status = "rendered" if code == 0 else "failed"
        detail["exit_code"] = code
    now = utc_now()
    record = DeploymentRecord(
        id=str(uuid4()),
        name=release,
        action="helm-template",
        status=status,
        actor="cli",
        command=command,
        output=output,
        created_at=now,
        updated_at=now,
        detail=detail,
    )
    SQLiteStore(db).save_deployment_record(record)
    typer.echo(pretty_json(record.model_dump(mode="json")))


@deploy_app.command("records")
def list_deployment_records(
    db: Annotated[Path, typer.Option("--db", help="SQLite database path.")] = DEFAULT_DB_PATH,
) -> None:
    """Print recent deployment orchestration records."""
    records = SQLiteStore(db).list_deployment_records()
    for record in records:
        typer.echo(f"{record.id}\t{record.action}\t{record.status}\t{' '.join(record.command)}")


@workers_app.command("build")
def build_workers(
    images: Annotated[
        Path,
        typer.Option("--images", help="Worker image map path."),
    ] = DEFAULT_IMAGES_PATH,
    kind: Annotated[
        str | None,
        typer.Option("--kind", help="Build only one goblin worker kind."),
    ] = None,
) -> None:
    """Build configured Docker worker images for local deployment."""
    worker_map = _load_workers(images)
    runtime = DockerRuntime(workers=worker_map)
    targets = [(kind, worker_map.get(kind))] if kind else worker_map.items()
    for worker_kind, worker in targets:
        runtime.build_image(worker_kind)
        typer.echo(f"built {worker_kind}\t{worker.image}")


@workers_app.command("validate")
def validate_worker_contracts(
    input_path: Annotated[Path, typer.Option("--input", help="JSON input payload path.")],
    registry: Annotated[
        Path | None,
        typer.Option("--registry", help="Registry JSON path."),
    ] = Path("goblins.json"),
    images: Annotated[
        Path | None,
        typer.Option("--images", help="Worker image map path."),
    ] = DEFAULT_IMAGES_PATH,
    project: Annotated[
        Path | None,
        typer.Option("--project", help="Project settings path to validate discovered workers."),
    ] = None,
    runtime: Annotated[
        ValidationRuntimeOption,
        typer.Option("--runtime", help="Validation runtime; Docker remains the default."),
    ] = "docker",
    kind: Annotated[
        list[str] | None,
        typer.Option("--kind", help="Validate only this goblin kind; repeatable."),
    ] = None,
    build: Annotated[
        bool,
        typer.Option("--build", help="Build worker images before running validation."),
    ] = False,
    require_success: Annotated[
        bool,
        typer.Option("--require-success", help="Treat failed result envelopes as invalid."),
    ] = False,
    timeout_seconds: Annotated[
        int | None,
        typer.Option("--timeout-seconds", help="Maximum worker execution seconds."),
    ] = None,
    redis_url: Annotated[
        str,
        typer.Option("--redis-url", help="Redis URL used by Docker result transport."),
    ] = DEFAULT_REDIS_URL,
    result_forwarder_image: ResultForwarderImageOption = DEFAULT_RESULT_FORWARDER_IMAGE,
    worker_image_pull_policy: WorkerImagePullPolicyOption = (
        DEFAULT_KUBERNETES_IMAGE_PULL_POLICY
    ),
    result_forwarder_image_pull_policy: ResultForwarderImagePullPolicyOption = (
        DEFAULT_KUBERNETES_IMAGE_PULL_POLICY
    ),
    workload_image_pull_secrets: WorkloadImagePullSecretsOption = None,
    kubernetes_runtime_settings_path: KubernetesRuntimeSettingsPathOption = None,
    run_root: DockerRunRootOption = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print machine-readable validation results."),
    ] = False,
    db: Annotated[Path, typer.Option("--db", help="SQLite database path.")] = DEFAULT_DB_PATH,
) -> None:
    """Run workers through Docker or Kubernetes and validate result envelopes."""
    if project is not None:
        loaded_registry = _load_project_registry(project)
        loaded_workers = _load_project_workers(_load_project_settings(project))
    else:
        if registry is None or images is None:
            typer.echo("--registry and --images are required unless --project is used", err=True)
            raise typer.Exit(1)
        loaded_registry = _load_registry(registry)
        loaded_workers = _load_workers(images)
    input_payload = _load_input(input_path)
    effective_kubernetes_settings: KubernetesRuntimeSettings | None = None
    if runtime == "kubernetes":
        if build:
            typer.echo("--build is available only with --runtime docker", err=True)
            raise typer.Exit(1)
        if run_root is not None:
            typer.echo("--run-root is available only with --runtime docker", err=True)
            raise typer.Exit(1)
        effective_kubernetes_settings = kubernetes_runtime_settings(
            result_forwarder_image=result_forwarder_image,
            worker_image_pull_policy=worker_image_pull_policy,
            result_forwarder_image_pull_policy=result_forwarder_image_pull_policy,
            workload_image_pull_secrets=workload_image_pull_secrets,
            settings_path=kubernetes_runtime_settings_path,
        )
        results = validate_workers_with_kubernetes(
            registry=loaded_registry,
            workers=loaded_workers,
            input_payload=input_payload,
            kinds=kind,
            require_success=require_success,
            timeout_seconds=timeout_seconds or 120,
            redis_url=redis_url,
            kubernetes_runtime_settings=effective_kubernetes_settings,
        )
    else:
        results = validate_workers(
            registry=loaded_registry,
            workers=loaded_workers,
            input_payload=input_payload,
            kinds=kind,
            build=build,
            require_success=require_success,
            timeout_seconds=timeout_seconds,
            redis_url=redis_url,
            run_root=run_root,
        )
    store = SQLiteStore(db)
    for result in results:
        effective_policy = (
            {
                "kubernetes_workload_security": (
                    effective_kubernetes_settings.effective_workload_security(result.kind)
                )
            }
            if effective_kubernetes_settings is not None
            else None
        )
        store.save_worker_validation(
            validation_record(result, effective_policy=effective_policy)
        )
    _print_validation_results(results, json_output=json_output)


@workers_app.command("validate-image")
def validate_worker_image(
    image: Annotated[str, typer.Option("--image", help="Prebuilt worker image to validate.")],
    input_path: Annotated[Path, typer.Option("--input", help="JSON input payload path.")],
    kind: Annotated[
        str,
        typer.Option("--kind", help="Temporary goblin kind used for validation."),
    ] = "adopter.validation",
    context: Annotated[
        Path,
        typer.Option("--context", help="Optional build context when --build is used."),
    ] = Path("."),
    dockerfile: Annotated[
        str,
        typer.Option("--dockerfile", help="Dockerfile name inside --context."),
    ] = "Dockerfile",
    build: Annotated[
        bool,
        typer.Option("--build", help="Build the image from --context before validation."),
    ] = False,
    require_success: Annotated[
        bool,
        typer.Option("--require-success", help="Treat failed result envelopes as invalid."),
    ] = False,
    timeout_seconds: Annotated[
        int | None,
        typer.Option("--timeout-seconds", help="Maximum worker execution seconds."),
    ] = None,
    redis_url: Annotated[
        str,
        typer.Option("--redis-url", help="Redis URL used by Docker result transport."),
    ] = DEFAULT_REDIS_URL,
    run_root: DockerRunRootOption = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print machine-readable validation results."),
    ] = False,
    db: Annotated[Path, typer.Option("--db", help="SQLite database path.")] = DEFAULT_DB_PATH,
) -> None:
    """Validate a one-off prebuilt worker image against the container contract."""
    registry = GoblinRegistry.from_definitions(
        [
            GoblinDefinition(
                kind=kind,
                display_name=kind,
                module="goblin_king.container_only",
            )
        ]
    )
    workers = WorkerImageMap.from_definitions(
        {
            kind: WorkerImageDefinition(
                context=context,
                dockerfile=dockerfile,
                image=image,
            )
        }
    )
    results = validate_workers(
        registry=registry,
        workers=workers,
        input_payload=_load_input(input_path),
        kinds=[kind],
        build=build,
        require_success=require_success,
        prebuilt_image=not build,
        timeout_seconds=timeout_seconds,
        redis_url=redis_url,
        run_root=run_root,
    )
    store = SQLiteStore(db)
    for result in results:
        store.save_worker_validation(validation_record(result))
    _print_validation_results(results, json_output=json_output)


@workers_app.command("validation-status")
def worker_validation_status(
    kind: Annotated[
        str | None,
        typer.Option("--kind", help="Filter validation records to one goblin kind."),
    ] = None,
    db: Annotated[Path, typer.Option("--db", help="SQLite database path.")] = DEFAULT_DB_PATH,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print machine-readable validation status."),
    ] = False,
) -> None:
    """List persisted worker validation records used by the scheduler gate."""
    records = SQLiteStore(db).list_worker_validations(kind=kind)
    if json_output:
        typer.echo(pretty_json([record.model_dump(mode="json") for record in records]))
        return
    for record in records:
        failures = ",".join(record.failure_reasons) if record.failure_reasons else "-"
        typer.echo(
            f"{record.kind}\t{record.status}\t{record.image_digest}\t"
            f"{record.contract_version}\t{record.validator_version}\t{failures}"
        )


@resource_policies_app.command("inspect")
def inspect_resource_policy(
    kind: Annotated[str, typer.Argument(help="Goblin kind whose effective policy is shown.")],
    policies: Annotated[
        Path,
        typer.Option("--policies", help="Resource policy JSON path."),
    ] = DEFAULT_RESOURCE_POLICIES_PATH,
    timeout_seconds: Annotated[
        int | None,
        typer.Option("--timeout-seconds", help="Optional job timeout override."),
    ] = None,
    max_retries: Annotated[
        int | None,
        typer.Option("--max-retries", help="Optional job retry override."),
    ] = None,
) -> None:
    """Print effective policy and runtime mappings used for local proof."""
    try:
        policy_set = ResourcePolicySet.from_path(policies)
        policy = policy_set.effective_for(
            kind,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
        )
    except ResourcePolicyError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from error
    payload = {
        "kind": kind,
        "effective_policy": policy.compact(),
        "docker_args": docker_policy_args(policy),
        "kubernetes_fields": kubernetes_policy_fields(policy),
        "artifact_policy": {
            "max_bytes": policy.filesystem.artifact_max_bytes,
            "max_files": policy.filesystem.artifact_max_files,
        },
        "log_policy": {"max_bytes": policy.logs.max_bytes},
    }
    typer.echo(pretty_json(payload))


def _print_demo_up_result(result) -> None:
    """Print a compact human-readable demo proof receipt."""
    typer.echo(f"ok\t{str(result.ok).lower()}")
    typer.echo(f"stage\t{result.stage}")
    typer.echo(f"admin_url\t{result.admin_url}")
    typer.echo(f"project\t{result.project}")
    typer.echo(f"kind\t{result.kind}")
    if result.validation is not None:
        validation_status = "passed" if result.validation.ok else "failed"
        typer.echo(f"validation\t{validation_status}\t{result.validation.image_digest or '-'}")
    if result.discovery is not None:
        active = result.discovery.get("active_goblin_count", "-")
        version = result.discovery.get("discovery_version", "-")
        typer.echo(f"discovery\tactive={active}\tversion={version}")
    if result.job is not None:
        typer.echo(f"job\t{result.job.get('id', '-')}\t{result.job.get('status', '-')}")
    if result.run is not None:
        typer.echo(f"run\t{result.run.get('id', '-')}\t{result.run.get('status', '-')}")
    if result.error:
        typer.echo(f"error\t{result.error}")
    typer.echo(f"cleanup\t{result.cleanup}")


if __name__ == "__main__":
    app()
