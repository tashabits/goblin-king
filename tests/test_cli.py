"""Local CLI tests for the Phase 1 vertical slice."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from goblin_king.cli import app
from goblin_king.contracts import EventRecord, HeartbeatRecord, JobRecord, utc_now
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


def test_project_goblins_list_and_validate() -> None:
    """Verify project commands load merged project settings."""
    listed = runner.invoke(
        app,
        ["project", "goblins", "list", "--project", "goblin-king-project.json"],
    )
    validated = runner.invoke(app, ["project", "validate", "--project", "goblin-king-project.json"])

    assert listed.exit_code == 0
    assert "example.echo" in listed.stdout
    assert validated.exit_code == 0
    assert "goblins\t7" in validated.stdout


def test_auth_setup_commands_create_user_project_and_token(tmp_path: Path) -> None:
    """Verify auth CLI commands create local users, projects, and hashed tokens."""
    db_path = tmp_path / "goblin.sqlite3"
    user = runner.invoke(
        app,
        [
            "auth",
            "create-user",
            "--email",
            "dev@example.test",
            "--display-name",
            "Dev",
            "--db",
            str(db_path),
        ],
    )
    project = runner.invoke(
        app,
        ["auth", "create-project", "--name", "Project A", "--db", str(db_path)],
    )
    token = runner.invoke(
        app,
        [
            "auth",
            "create-token",
            "--name",
            "token",
            "--user-id",
            json.loads(user.stdout)["id"],
            "--project-id",
            json.loads(project.stdout)["id"],
            "--db",
            str(db_path),
        ],
    )

    assert user.exit_code == 0
    assert project.exit_code == 0
    assert token.exit_code == 0
    assert json.loads(token.stdout)["raw_token"].startswith("gk_")


def test_project_init_package_creates_template(tmp_path: Path) -> None:
    """Verify the package template generator is reachable from the CLI."""
    result = runner.invoke(
        app,
        [
            "project",
            "init-package",
            str(tmp_path / "generated"),
            "--kind",
            "sample.echo",
            "--package-name",
            "sample_echo",
            "--image",
            "sample-echo:local",
        ],
    )

    assert result.exit_code == 0
    assert (tmp_path / "generated" / "pyproject.toml").exists()


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


def test_jobs_fanout_and_fanouts_show(tmp_path: Path) -> None:
    """Verify CLI fanout creates a batch and fanout read commands display it."""
    db_path = tmp_path / "goblin.sqlite3"
    fanout_path = tmp_path / "fanout.json"
    fanout_path.write_text(
        json.dumps(
            {
                "description": "cli fanout",
                "items": [
                    {"kind": "example.echo", "input": {"message": "one"}},
                    {"kind": "example.echo", "input": {"message": "two"}},
                ],
            }
        ),
        encoding="utf-8",
    )

    created = runner.invoke(
        app,
        [
            "jobs",
            "fanout",
            "--input",
            str(fanout_path),
            "--registry",
            "examples/goblins.json",
            "--db",
            str(db_path),
        ],
    )
    fanout_id = json.loads(created.stdout)["fanout"]["id"]
    listed = runner.invoke(app, ["fanouts", "list", "--db", str(db_path)])
    shown = runner.invoke(app, ["fanouts", "show", fanout_id, "--db", str(db_path)])

    assert created.exit_code == 0
    assert "queued" in listed.stdout
    assert json.loads(shown.stdout)["counts"]["total"] == 2


def test_jobs_retry_creates_queued_retry(tmp_path: Path) -> None:
    """Verify CLI retry creates a queued retry job from a terminal source."""
    db_path = tmp_path / "goblin.sqlite3"
    store = SQLiteStore(db_path)
    store.save_job(
        JobRecord(
            id="source",
            kind="example.echo",
            input={"message": "retry me"},
            created_at=utc_now(),
            status="failed",
        )
    )

    result = runner.invoke(
        app,
        ["jobs", "retry", "source", "--db", str(db_path), "--reason", "manual"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "queued"
    assert payload["metadata"]["retry"]["source_job_id"] == "source"


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


def test_events_and_heartbeats_list_commands(tmp_path: Path) -> None:
    """Verify CLI inspection commands print durable events and heartbeats."""
    db_path = tmp_path / "goblin.sqlite3"
    store = SQLiteStore(db_path)
    store.save_event(
        EventRecord(
            id="event-1",
            created_at=utc_now(),
            event_type="job.completed",
            source="scheduler",
            job_id="job-1",
        )
    )
    store.upsert_heartbeat(
        HeartbeatRecord(
            owner_id="scheduler-1",
            owner_type="scheduler",
            status="running",
            last_seen_at=utc_now(),
        )
    )

    events = runner.invoke(app, ["events", "list", "--db", str(db_path), "--limit", "1"])
    heartbeats = runner.invoke(app, ["heartbeats", "list", "--db", str(db_path)])

    assert events.exit_code == 0
    assert json.loads(events.stdout)["event_type"] == "job.completed"
    assert heartbeats.exit_code == 0
    assert json.loads(heartbeats.stdout)["owner_id"] == "scheduler-1"
