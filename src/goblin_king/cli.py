"""Typer command line interface for the Phase 1 Goblin King kernel."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated
from uuid import uuid4

import typer

from goblin_king.contracts import GoblinContext, JobRecord, RunRecord, utc_now
from goblin_king.registry import GoblinRegistry, RegistryError
from goblin_king.runtime import InProcessRuntime
from goblin_king.store import DEFAULT_DB_PATH, SQLiteStore

app = typer.Typer(help="Run and inspect Goblin King jobs.")
goblins_app = typer.Typer(help="Inspect registered goblins.")
jobs_app = typer.Typer(help="Submit goblin jobs.")
runs_app = typer.Typer(help="Inspect goblin runs.")
app.add_typer(goblins_app, name="goblins")
app.add_typer(jobs_app, name="jobs")
app.add_typer(runs_app, name="runs")


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

    typer.echo(run.model_dump_json(indent=2))
    if run.status == "failed":
        raise typer.Exit(1)


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
