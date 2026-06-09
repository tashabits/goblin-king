"""Typer command line interface for the Phase 1 Goblin King kernel."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated
from uuid import uuid4

import typer

from goblin_king.contracts import GoblinContext, JobRecord, RunRecord, ScheduleRecord, utc_now
from goblin_king.registry import GoblinRegistry, RegistryError
from goblin_king.runtime import InProcessRuntime
from goblin_king.scheduler import DEFAULT_INTERVAL_SECONDS, Scheduler, next_run_after
from goblin_king.store import DEFAULT_DB_PATH, SQLiteStore

app = typer.Typer(help="Run and inspect Goblin King jobs.")
goblins_app = typer.Typer(help="Inspect registered goblins.")
jobs_app = typer.Typer(help="Submit goblin jobs.")
runs_app = typer.Typer(help="Inspect goblin runs.")
schedules_app = typer.Typer(help="Create and inspect schedules.")
scheduler_app = typer.Typer(help="Run scheduler passes.")
app.add_typer(goblins_app, name="goblins")
app.add_typer(jobs_app, name="jobs")
app.add_typer(runs_app, name="runs")
app.add_typer(schedules_app, name="schedules")
app.add_typer(scheduler_app, name="scheduler")


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


@jobs_app.command("submit")
def submit_job(
    kind: Annotated[str, typer.Argument(help="Goblin kind to execute.")],
    input_path: Annotated[Path, typer.Option("--input", help="JSON input payload path.")],
    registry: Annotated[
        Path,
        typer.Option("--registry", help="Registry JSON path."),
    ] = Path("goblins.json"),
    db: Annotated[Path, typer.Option("--db", help="SQLite database path.")] = DEFAULT_DB_PATH,
) -> None:
    """Submit and immediately execute one job through the in-process runtime."""
    loaded = _load_registry(registry)
    input_payload = _load_input(input_path)
    try:
        definition, entrypoint = loaded.resolve(kind)
    except RegistryError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from error
    store = SQLiteStore(db)

    job = JobRecord(
        id=str(uuid4()),
        kind=definition.kind,
        input=input_payload,
        created_at=utc_now(),
    )
    run_id = str(uuid4())
    context = GoblinContext(
        run_id=run_id,
        artifact_root=str(Path(".goblin-king") / "artifacts" / run_id),
        metadata={"job_id": job.id, "kind": definition.kind},
    )

    store.save_job(job)
    started_at = utc_now()
    result = InProcessRuntime().run(definition, entrypoint, input_payload, context)
    finished_at = utc_now()
    run = RunRecord(
        id=run_id,
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


@jobs_app.command("list")
def list_jobs(
    db: Annotated[Path, typer.Option("--db", help="SQLite database path.")] = DEFAULT_DB_PATH,
) -> None:
    """Print persisted jobs in creation order."""
    store = SQLiteStore(db)
    for job in store.list_jobs():
        typer.echo(f"{job.id}\t{job.kind}\t{job.status}\t{job.due_at or ''}")


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
    db: Annotated[Path, typer.Option("--db", help="SQLite database path.")] = DEFAULT_DB_PATH,
) -> None:
    """Run one deterministic scheduler pass and print any created runs."""
    scheduler = Scheduler(registry=_load_registry(registry), store=SQLiteStore(db))
    runs = scheduler.run_once()
    typer.echo(json.dumps([run.model_dump(mode="json") for run in runs], indent=2))


@scheduler_app.command("run")
def scheduler_run(
    registry: Annotated[
        Path,
        typer.Option("--registry", help="Registry JSON path."),
    ] = Path("goblins.json"),
    db: Annotated[Path, typer.Option("--db", help="SQLite database path.")] = DEFAULT_DB_PATH,
    interval_seconds: Annotated[
        int,
        typer.Option("--interval-seconds", help="Seconds between scheduler passes."),
    ] = DEFAULT_INTERVAL_SECONDS,
) -> None:
    """Run scheduler passes until interrupted."""
    scheduler = Scheduler(registry=_load_registry(registry), store=SQLiteStore(db))
    try:
        scheduler.run_loop(interval_seconds=interval_seconds)
    except KeyboardInterrupt:
        typer.echo("scheduler stopped")


def _load_registry(path: Path) -> GoblinRegistry:
    """Load a registry for CLI commands and translate registry errors into process exits."""
    try:
        return GoblinRegistry.from_path(path)
    except RegistryError as error:
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


if __name__ == "__main__":
    app()
