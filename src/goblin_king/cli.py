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
from goblin_king.contracts import JobRecord, RunRecord, ScheduleRecord, utc_now
from goblin_king.events import DEFAULT_EVENT_CHANNEL
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
from goblin_king.runtime import DockerRuntime, InProcessRuntime, KubernetesRuntime, new_run_context
from goblin_king.scheduler import DEFAULT_INTERVAL_SECONDS, Scheduler, next_run_after
from goblin_king.store import DEFAULT_DB_PATH, SQLiteStore
from goblin_king.templates import TemplateError, init_package
from goblin_king.workers import WorkerConfigError, WorkerImageMap

app = typer.Typer(help="Run and inspect Goblin King jobs.")
api_app = typer.Typer(help="Run the HTTP API control plane.")
auth_app = typer.Typer(help="Manage local API users, projects, and tokens.")
goblins_app = typer.Typer(help="Inspect registered goblins.")
jobs_app = typer.Typer(help="Submit goblin jobs.")
fanouts_app = typer.Typer(help="Inspect fanout batches.")
events_app = typer.Typer(help="Inspect and watch durable events.")
heartbeats_app = typer.Typer(help="Inspect scheduler and worker heartbeats.")
project_app = typer.Typer(help="Inspect and scaffold reusable Goblin King projects.")
project_goblins_app = typer.Typer(help="Inspect project-discovered goblins.")
runs_app = typer.Typer(help="Inspect goblin runs.")
schedules_app = typer.Typer(help="Create and inspect schedules.")
scheduler_app = typer.Typer(help="Run scheduler passes.")
workers_app = typer.Typer(help="Build Docker worker images.")
app.add_typer(api_app, name="api")
app.add_typer(auth_app, name="auth")
app.add_typer(goblins_app, name="goblins")
app.add_typer(jobs_app, name="jobs")
app.add_typer(fanouts_app, name="fanouts")
app.add_typer(events_app, name="events")
app.add_typer(heartbeats_app, name="heartbeats")
project_app.add_typer(project_goblins_app, name="goblins")
app.add_typer(project_app, name="project")
app.add_typer(runs_app, name="runs")
app.add_typer(schedules_app, name="schedules")
app.add_typer(scheduler_app, name="scheduler")
app.add_typer(workers_app, name="workers")

RuntimeOption = Literal["docker", "kubernetes", "in-process"]
DEFAULT_IMAGES_PATH = Path("goblin-images.json")
DEFAULT_REDIS_URL = "redis://localhost:6379/0"
DEFAULT_PROJECT_PATH = Path("goblin-king-project.json")


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
    workers = _load_workers(settings.images)
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


@jobs_app.command("submit")
def submit_job(
    kind: Annotated[str, typer.Argument(help="Goblin kind to execute.")],
    input_path: Annotated[Path, typer.Option("--input", help="JSON input payload path.")],
    registry: Annotated[
        Path,
        typer.Option("--registry", help="Registry JSON path."),
    ] = Path("goblins.json"),
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
) -> None:
    """Submit and immediately execute one job through the selected runtime."""
    loaded = _load_registry(registry)
    input_payload = _load_input(input_path)
    try:
        definition = loaded.get(kind)
        entrypoint = loaded.resolve(kind)[1] if runtime == "in-process" else None
    except (RegistryError, WorkerConfigError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from error
    store = SQLiteStore(db)

    job = JobRecord(
        id=str(uuid4()),
        kind=definition.kind,
        input=input_payload,
        created_at=utc_now(),
    )
    context = new_run_context(job.id, definition.kind)

    store.save_job(job)
    started_at = utc_now()
    if runtime == "docker":
        result = DockerRuntime(
            workers=_load_workers(images),
            redis_url=redis_url,
        ).run(
            definition,
            entrypoint,
            input_payload,
            context,
            timeout_seconds=definition.timeout_seconds,
        )
    elif runtime == "kubernetes":
        result = KubernetesRuntime(
            workers=_load_workers(images),
            redis_url=redis_url,
        ).run(
            definition,
            entrypoint,
            input_payload,
            context,
            timeout_seconds=definition.timeout_seconds,
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
) -> None:
    """Create a queued fanout batch from a JSON request."""
    request = FanoutCreateRequest.model_validate(_load_input(input_path))
    detail = create_fanout(
        store=SQLiteStore(db),
        registry=_load_registry(registry),
        request=request,
        created_by="cli",
    )
    typer.echo(detail.model_dump_json(indent=2))


@jobs_app.command("retry")
def retry_cli_job(
    job_id: Annotated[str, typer.Argument(help="Source job ID to retry.")],
    db: Annotated[Path, typer.Option("--db", help="SQLite database path.")] = DEFAULT_DB_PATH,
    reason: Annotated[str | None, typer.Option("--reason", help="Retry reason.")] = None,
) -> None:
    """Create a queued retry job from a terminal source job."""
    try:
        retry = retry_job(
            store=SQLiteStore(db),
            job_id=job_id,
            request=RetryCreateRequest(reason=reason),
            created_by="cli-retry",
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
) -> None:
    """Print a persisted run record as JSON."""
    store = SQLiteStore(db)
    run = store.get_run(run_id)
    if run is None:
        typer.echo(f"run not found: {run_id}", err=True)
        raise typer.Exit(1)
    typer.echo(run.model_dump_json(indent=2))


@schedules_app.command("add")
def add_schedule(
    kind: Annotated[str, typer.Argument(help="Goblin kind to schedule.")],
    cron: Annotated[str, typer.Option("--cron", help="Cron expression.")],
    input_path: Annotated[Path, typer.Option("--input", help="JSON input payload path.")],
    registry: Annotated[
        Path,
        typer.Option("--registry", help="Registry JSON path."),
    ] = Path("goblins.json"),
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
) -> None:
    """Persist a recurring goblin schedule."""
    loaded = _load_registry(registry)
    try:
        definition = loaded.get(kind)
    except RegistryError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from error
    input_payload = _load_input(input_path)
    created_at = utc_now()
    provisional = ScheduleRecord(
        id=str(uuid4()),
        kind=definition.kind,
        input=input_payload,
        cron=cron,
        timezone=timezone,
        created_at=created_at,
        next_run_at=created_at,
        max_retries=max_retries,
        timeout_seconds=timeout_seconds,
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
) -> None:
    """Run one deterministic scheduler pass and print any created runs."""
    registry, workers = _load_scheduler_discovery(registry, images, project, runtime)
    scheduler = Scheduler(
        registry=registry,
        store=SQLiteStore(db),
        runtime_mode=runtime,
        workers=workers,
        redis_url=redis_url,
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
) -> None:
    """Run scheduler passes until interrupted."""
    registry, workers = _load_scheduler_discovery(registry, images, project, runtime)
    scheduler = Scheduler(
        registry=registry,
        store=SQLiteStore(db),
        runtime_mode=runtime,
        workers=workers,
        redis_url=redis_url,
    )
    try:
        scheduler.run_loop(interval_seconds=interval_seconds)
    except KeyboardInterrupt:
        typer.echo("scheduler stopped")


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
        workers = _load_workers(settings.images) if runtime in {"docker", "kubernetes"} else None
        return registry, workers
    registry = _load_registry(registry_path)
    workers = _load_workers(images_path) if runtime in {"docker", "kubernetes"} else None
    return registry, workers


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


if __name__ == "__main__":
    app()
