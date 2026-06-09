"""Local CLI tests for the Phase 1 vertical slice."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from goblin_king.cli import app
from goblin_king.store import SQLiteStore

runner = CliRunner()


def write_registry(path: Path, kind: str, module: str) -> Path:
    """Write a one-goblin registry fixture for CLI tests."""
    path.write_text(
        json.dumps(
            {
                "goblins": [
                    {
                        "kind": kind,
                        "display_name": kind,
                        "module": module,
                        "entrypoint": "run",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return path


def test_goblins_list_prints_example_echo() -> None:
    """Verify the CLI lists registered goblins."""
    result = runner.invoke(app, ["goblins", "list", "--registry", "examples/goblins.json"])

    assert result.exit_code == 0
    assert "example.echo" in result.stdout


def test_jobs_submit_persists_completed_run(tmp_path: Path) -> None:
    """Verify a successful CLI submit prints and persists a completed run."""
    db_path = tmp_path / "goblin.sqlite3"
    result = runner.invoke(
        app,
        [
            "jobs",
            "submit",
            "example.echo",
            "--input",
            "examples/input.json",
            "--registry",
            "examples/goblins.json",
            "--db",
            str(db_path),
            "--runtime",
            "in-process",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "completed"
    loaded = SQLiteStore(db_path).get_run(payload["id"])
    assert loaded is not None
    assert loaded.status == "completed"


def test_jobs_submit_persists_failed_run(tmp_path: Path) -> None:
    """Verify a failing CLI submit exits nonzero and persists failed status."""
    registry_path = write_registry(
        tmp_path / "goblins.json",
        "example.fail",
        "examples.goblins.failing",
    )
    input_path = tmp_path / "input.json"
    input_path.write_text("{}", encoding="utf-8")
    db_path = tmp_path / "goblin.sqlite3"

    result = runner.invoke(
        app,
        [
            "jobs",
            "submit",
            "example.fail",
            "--input",
            str(input_path),
            "--registry",
            str(registry_path),
            "--db",
            str(db_path),
            "--runtime",
            "in-process",
        ],
    )

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["status"] == "failed"
    loaded = SQLiteStore(db_path).get_run(payload["id"])
    assert loaded is not None
    assert loaded.status == "failed"


def test_runs_show_prints_stored_run(tmp_path: Path) -> None:
    """Verify the CLI can inspect a run persisted by a previous submit."""
    db_path = tmp_path / "goblin.sqlite3"
    submit = runner.invoke(
        app,
        [
            "jobs",
            "submit",
            "example.echo",
            "--input",
            "examples/input.json",
            "--registry",
            "examples/goblins.json",
            "--db",
            str(db_path),
            "--runtime",
            "in-process",
        ],
    )
    run_id = json.loads(submit.stdout)["id"]

    result = runner.invoke(app, ["runs", "show", run_id, "--db", str(db_path)])

    assert result.exit_code == 0
    assert json.loads(result.stdout)["id"] == run_id


def test_schedules_add_and_list_persist_schedule(tmp_path: Path) -> None:
    """Verify schedule CLI commands can create and display a recurring schedule."""
    db_path = tmp_path / "goblin.sqlite3"

    add = runner.invoke(
        app,
        [
            "schedules",
            "add",
            "example.echo",
            "--cron",
            "* * * * *",
            "--input",
            "examples/input.json",
            "--registry",
            "examples/goblins.json",
            "--db",
            str(db_path),
            "--due-now",
        ],
    )
    listed = runner.invoke(app, ["schedules", "list", "--db", str(db_path)])

    assert add.exit_code == 0
    assert json.loads(add.stdout)["kind"] == "example.echo"
    assert listed.exit_code == 0
    assert "example.echo" in listed.stdout


def test_scheduler_run_once_executes_due_schedule(tmp_path: Path) -> None:
    """Verify scheduler CLI run-once executes a due schedule and jobs list shows completion."""
    db_path = tmp_path / "goblin.sqlite3"
    add = runner.invoke(
        app,
        [
            "schedules",
            "add",
            "example.echo",
            "--cron",
            "* * * * *",
            "--input",
            "examples/input.json",
            "--registry",
            "examples/goblins.json",
            "--db",
            str(db_path),
            "--due-now",
        ],
    )
    assert add.exit_code == 0

    run_once = runner.invoke(
        app,
        [
            "scheduler",
            "run-once",
            "--registry",
            "examples/goblins.json",
            "--db",
            str(db_path),
            "--runtime",
            "in-process",
        ],
    )
    jobs = runner.invoke(app, ["jobs", "list", "--db", str(db_path)])

    assert run_once.exit_code == 0
    assert json.loads(run_once.stdout)[0]["status"] == "completed"
    assert jobs.exit_code == 0
    assert "completed" in jobs.stdout
