"""Typer command line interface for the Phase 1 Goblin King kernel."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import uuid4

import typer
from redis import Redis
from redis.exceptions import RedisError

from goblin_king.auth import create_api_token, create_project, create_user
from goblin_king.contracts import (
    DeploymentRecord,
    GoblinDefinition,
    ImagePromotionRecord,
    JobRecord,
    RunRecord,
    ScheduleRecord,
    utc_now,
)
from goblin_king.deployment import helm_template_command, image_push_command, run_command
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
from goblin_king.project import ProjectSettings, ProjectSettingsError
from goblin_king.registry import GoblinRegistry, RegistryError
from goblin_king.resource_policies import ResourcePolicyError, ResourcePolicySet
from goblin_king.runtime import DockerRuntime, InProcessRuntime, KubernetesRuntime, new_run_context
from goblin_king.scheduler import DEFAULT_INTERVAL_SECONDS, Scheduler, next_run_after
from goblin_king.smoke import run_adopter_project_smoke
from goblin_king.store import DEFAULT_DB_PATH, SQLiteStore
from goblin_king.templates import TemplateError, init_package, init_project
from goblin_king.validation import WorkerValidationResult, validate_workers
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
project_app = typer.Typer(help="Inspect and scaffold reusable Goblin King projects.")
project_goblins_app = typer.Typer(help="Inspect project-discovered goblins.")
runs_app = typer.Typer(help="Inspect goblin runs.")
schedules_app = typer.Typer(help="Create and inspect schedules.")
scheduler_app = typer.Typer(help="Run scheduler passes.")
smoke_app = typer.Typer(help="Run local end-to-end smoke proofs.")
workers_app = typer.Typer(help="Build Docker worker images.")
app.add_typer(api_app, name="api")
app.add_typer(auth_app, name="auth")
app.add_typer(goblins_app, name="goblins")
app.add_typer(jobs_app, name="jobs")
app.add_typer(fanouts_app, name="fanouts")
app.add_typer(events_app, name="events")
app.add_typer(heartbeats_app, name="heartbeats")
deploy_app.add_typer(deploy_promotions_app, name="promotions")
app.add_typer(deploy_app, name="deploy")
project_app.add_typer(project_goblins_app, name="goblins")
app.add_typer(project_app, name="project")
app.add_typer(runs_app, name="runs")
app.add_typer(schedules_app, name="schedules")
app.add_typer(scheduler_app, name="scheduler")
app.add_typer(smoke_app, name="smoke")
app.add_typer(workers_app, name="workers")

RuntimeOption = Literal["docker", "kubernetes", "in-process"]
DEFAULT_IMAGES_PATH = Path("goblin-images.json")
DEFAULT_REDIS_URL = "redis://localhost:6379/0"
DEFAULT_PROJECT_PATH = Path("goblin-king-project.json")
DEFAULT_RESOURCE_POLICIES_PATH = Path("goblin-resource-policies.json")


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
    typer.echo(json.dumps({"token": token.model_dump(mode="json"), "raw_token": raw}, indent=2))


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
    typer.echo(f"workers\t{len(workers.items())}")
    typer.echo(f"worker_coverage\t{len(registry.list())}/{len(registry.list())}")
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
) -> None:
    """Generate a standalone adopter project with contract-compliant workers."""
    try:
        created = init_project(target_dir, prefix=prefix)
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
    resource_policies: Annotated[
        Path | None,
        typer.Option("--resource-policies", help="Optional resource policy JSON path."),
    ] = DEFAULT_RESOURCE_POLICIES_PATH,
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
    policy_set = _load_resource_policies(resource_policies)
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
        metadata=_job_metadata(definition, policy),
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
        ).run(
            definition,
            entrypoint,
            input_payload,
            context,
            timeout_seconds=job.timeout_seconds,
            resource_policy=policy,
        )
    elif runtime == "kubernetes":
        result = KubernetesRuntime(
            workers=worker_map,
            redis_url=redis_url,
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
    finished_at = utc_now()
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
    typer.echo(json.dumps(stream_status(redis_url, stream=stream), indent=2))


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
    policy_set = _load_resource_policies(resource_policies)
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
    resource_policies: Annotated[
        Path | None,
        typer.Option("--resource-policies", help="Optional resource policy JSON path."),
    ] = DEFAULT_RESOURCE_POLICIES_PATH,
) -> None:
    """Run one deterministic scheduler pass and print any created runs."""
    registry, workers = _load_scheduler_discovery(registry, images, project, runtime)
    scheduler = Scheduler(
        registry=registry,
        store=SQLiteStore(db),
        runtime_mode=runtime,
        workers=workers,
        redis_url=redis_url,
        resource_policies=_load_resource_policies(resource_policies),
    )
    runs = scheduler.run_once()
    typer.echo(json.dumps([run.model_dump(mode="json") for run in runs], indent=2))


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
    resource_policies: Annotated[
        Path | None,
        typer.Option("--resource-policies", help="Optional resource policy JSON path."),
    ] = DEFAULT_RESOURCE_POLICIES_PATH,
) -> None:
    """Run scheduler passes until interrupted."""
    registry, workers = _load_scheduler_discovery(registry, images, project, runtime)
    scheduler = Scheduler(
        registry=registry,
        store=SQLiteStore(db),
        runtime_mode=runtime,
        workers=workers,
        redis_url=redis_url,
        resource_policies=_load_resource_policies(resource_policies),
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
    typer.echo(json.dumps(promotion.model_dump(mode="json"), indent=2))


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
    typer.echo(json.dumps(promotion.model_dump(mode="json"), indent=2))


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
    typer.echo(json.dumps(record.model_dump(mode="json"), indent=2))


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
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print machine-readable validation results."),
    ] = False,
) -> None:
    """Run Docker workers with temp contract mounts and validate result envelopes."""
    if project is not None:
        loaded_registry = _load_project_registry(project)
        loaded_workers = _load_project_workers(ProjectSettings.from_path(project))
    else:
        if registry is None or images is None:
            typer.echo("--registry and --images are required unless --project is used", err=True)
            raise typer.Exit(1)
        loaded_registry = _load_registry(registry)
        loaded_workers = _load_workers(images)
    results = validate_workers(
        registry=loaded_registry,
        workers=loaded_workers,
        input_payload=_load_input(input_path),
        kinds=kind,
        build=build,
        require_success=require_success,
        timeout_seconds=timeout_seconds,
        redis_url=redis_url,
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
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print machine-readable validation results."),
    ] = False,
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
    )
    _print_validation_results(results, json_output=json_output)


def _print_validation_results(
    results: list[WorkerValidationResult],
    *,
    json_output: bool,
) -> None:
    """Print validation results and exit nonzero when any contract check fails."""
    if json_output:
        typer.echo(
            json.dumps([result.model_dump(mode="json") for result in results], indent=2)
        )
    else:
        for result in results:
            status = "ok" if result.ok else "failed"
            detail = result.error or ",".join(result.checks)
            typer.echo(f"{result.kind}\t{status}\t{result.result_status or '-'}\t{detail}")
    if any(not result.ok for result in results):
        raise typer.Exit(1)


def _load_registry(path: Path) -> GoblinRegistry:
    """Load a registry for CLI commands and translate registry errors into process exits."""
    try:
        return GoblinRegistry.from_path(path)
    except RegistryError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from error


def _load_workers(path: Path) -> WorkerImageMap:
    """Load worker image settings and translate errors into CLI exits."""
    try:
        return WorkerImageMap.from_path(path)
    except WorkerConfigError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from error


def _load_project_settings(path: Path) -> ProjectSettings:
    """Load project settings and translate errors into CLI exits."""
    try:
        return ProjectSettings.from_path(path)
    except ProjectSettingsError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from error


def _load_project_registry(path: Path) -> GoblinRegistry:
    """Load all goblins described by project settings."""
    settings = _load_project_settings(path)
    try:
        return GoblinRegistry.from_project_sources(
            settings.registries,
            include_entry_points=settings.entry_points,
            definitions=settings.registry_definitions(),
        )
    except RegistryError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from error


def _load_scheduler_discovery(
    registry_path: Path,
    images_path: Path,
    project_path: Path | None,
    runtime: RuntimeOption,
) -> tuple[GoblinRegistry, WorkerImageMap | None]:
    """Load scheduler registry and worker map from either direct paths or project settings."""
    if project_path is not None:
        settings = _load_project_settings(project_path)
        registry = _load_project_registry(project_path)
        workers = (
            _load_project_workers(settings)
            if runtime in {"docker", "kubernetes"}
            else None
        )
        return registry, workers
    registry = _load_registry(registry_path)
    workers = _load_workers(images_path) if runtime in {"docker", "kubernetes"} else None
    return registry, workers


def _load_resource_policies(path: Path | None) -> ResourcePolicySet | None:
    """Load optional resource policies; missing default files mean enforcement is off."""
    if path is None:
        return None
    if not path.exists() and path == DEFAULT_RESOURCE_POLICIES_PATH:
        return None
    try:
        return ResourcePolicySet.from_path(path)
    except ResourcePolicyError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from error


def _load_project_workers(settings: ProjectSettings) -> WorkerImageMap:
    """Load worker images plus inline project-config worker definitions."""
    try:
        return WorkerImageMap.from_path_and_definitions(
            settings.images,
            settings.worker_definitions(),
        )
    except WorkerConfigError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from error


def _load_input(path: Path) -> dict:
    """Load one JSON object from disk for a goblin invocation."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        typer.echo(f"input not found: {path}", err=True)
        raise typer.Exit(1) from error
    except json.JSONDecodeError as error:
        typer.echo(f"input is not valid JSON: {path}", err=True)
        raise typer.Exit(1) from error
    if not isinstance(payload, dict):
        typer.echo("input JSON must be an object", err=True)
        raise typer.Exit(1)
    return payload


def _job_metadata(definition: GoblinDefinition, policy: Any | None = None) -> dict:
    """Return metadata that explains the effective goblin used for one job."""
    metadata = {
        "goblin_source": definition.metadata.get("source", "registry"),
        "goblin_definition": definition.model_dump(mode="json"),
    }
    if policy is not None:
        metadata["resource_policy"] = policy.compact()
    return metadata


if __name__ == "__main__":
    app()
