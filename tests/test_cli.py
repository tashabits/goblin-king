"""Local CLI tests for the Phase 1 vertical slice."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from goblin_king.cli import app
from goblin_king.contracts import (
    EventRecord,
    HeartbeatRecord,
    JobRecord,
    ScheduleRecord,
    WorkerValidationRecord,
    utc_now,
)
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


def test_workers_validate_reports_unknown_kind() -> None:
    """Verify contract validation reports unknown requested kinds clearly."""
    result = runner.invoke(
        app,
        [
            "workers",
            "validate",
            "--registry",
            "examples/cross-language-goblins.json",
            "--images",
            "examples/cross-language-images.json",
            "--input",
            "examples/cross-language-input.json",
            "--kind",
            "example.missing",
            "--json",
        ],
    )

    assert result.exit_code == 1
    assert "unknown goblin kind: example.missing" in result.stdout


def test_workers_validate_accepts_project_settings() -> None:
    """Verify worker validation can use project settings discovery."""
    result = runner.invoke(
        app,
        [
            "workers",
            "validate",
            "--project",
            "examples/adopting-project/goblin-king-project.json",
            "--input",
            "examples/input.json",
            "--kind",
            "example.missing",
        ],
    )

    assert result.exit_code == 1
    assert "unknown goblin kind: example.missing" in result.stdout


def test_workers_validate_image_reports_missing_image(monkeypatch) -> None:
    """Verify direct image validation reports unavailable prebuilt images clearly."""

    def fake_validate_workers(**_kwargs):
        from goblin_king.validation import WorkerValidationResult

        return [
            WorkerValidationResult(
                kind="adopter.validation",
                ok=False,
                image="missing:local",
                error="worker image unavailable: missing:local",
            )
        ]

    monkeypatch.setattr("goblin_king.cli.validate_workers", fake_validate_workers)
    result = runner.invoke(
        app,
        [
            "workers",
            "validate-image",
            "--image",
            "missing:local",
            "--input",
            "examples/input.json",
        ],
    )

    assert result.exit_code == 1
    assert "worker image unavailable: missing:local" in result.stdout


def test_workers_validation_status_lists_persisted_records(tmp_path: Path) -> None:
    """Verify operators can inspect persisted scheduler validation proof."""
    db_path = tmp_path / "goblin.sqlite3"
    SQLiteStore(db_path).save_worker_validation(
        WorkerValidationRecord(
            id="validation-1",
            kind="example.hello",
            image="example:local",
            image_digest="sha256:abc",
            contract_version="goblin-king/v1alpha1",
            validator_version="goblin-king-validator/v1",
            validated_at=utc_now(),
            status="passed",
        )
    )

    result = runner.invoke(app, ["workers", "validation-status", "--db", str(db_path)])

    assert result.exit_code == 0
    assert "example.hello\tpassed\tsha256:abc" in result.stdout


def test_resource_policies_inspect_prints_runtime_mappings() -> None:
    """Verify operators can inspect effective policy proof for both runtimes."""
    result = runner.invoke(
        app,
        [
            "resource-policies",
            "inspect",
            "example.hello",
            "--policies",
            "examples/resource-policy-proof.json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["kind"] == "example.hello"
    assert payload["effective_policy"]["timeout_seconds"] == 30
    assert "--cpus" in payload["docker_args"]
    assert "--memory" in payload["docker_args"]
    assert "--pids-limit" in payload["docker_args"]
    assert ["--network", "none"] == payload["docker_args"][
        payload["docker_args"].index("--network") : payload["docker_args"].index("--network")
        + 2
    ]
    assert "--read-only" in payload["docker_args"]
    assert "--tmpfs" in payload["docker_args"]
    assert "--log-opt" in payload["docker_args"]
    assert payload["kubernetes_fields"]["resources"]["limits"]["cpu"] == "500m"
    assert payload["artifact_policy"] == {"max_bytes": 1048576, "max_files": 5}
    assert payload["log_policy"] == {"max_bytes": 2048}


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
    assert "goblins\t24" in validated.stdout
    assert "workers\t26" in validated.stdout
    assert "worker_coverage\t24/24" in validated.stdout
    assert "dockerfiles\tok" in validated.stdout


def test_project_validate_shows_default_resources(tmp_path: Path) -> None:
    """Verify project validation exposes raw defaults.resources when configured."""
    worker_dir = tmp_path / "worker"
    worker_dir.mkdir()
    (worker_dir / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    (tmp_path / "registry.json").write_text(
        json.dumps(
            {
                "goblins": [
                    {
                        "kind": "project.echo",
                        "display_name": "Project Echo",
                        "module": "examples.goblins.echo",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "images.json").write_text(
        json.dumps(
            {
                "workers": {
                    "project.echo": {
                        "context": "worker",
                        "dockerfile": "Dockerfile",
                        "image": "project-echo:local",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    project_path = tmp_path / "goblin-king-project.json"
    project_path.write_text(
        json.dumps(
            {
                "registries": ["registry.json"],
                "entry_points": False,
                "images": "images.json",
                "api_settings": "api.json",
                "defaults": {
                    "resources": {
                        "timeout_seconds": 45,
                        "memory": {"limit": "512Mi"},
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["project", "validate", "--project", str(project_path)])

    assert result.exit_code == 0
    assert (
        'defaults.resources\t{"memory": {"limit": "512Mi"}, "timeout_seconds": 45}'
        in result.stdout
    )


def test_jobs_submit_applies_project_default_resources(tmp_path: Path) -> None:
    """Verify project defaults become the effective policy for submitted jobs."""
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(
        json.dumps(
            {
                "goblins": [
                    {
                        "kind": "project.echo",
                        "display_name": "Project Echo",
                        "module": "examples.goblins.echo",
                        "entrypoint": "run",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    project_path = tmp_path / "goblin-king-project.json"
    project_path.write_text(
        json.dumps(
            {
                "registries": ["registry.json"],
                "entry_points": False,
                "defaults": {
                    "resources": {
                        "timeout_seconds": 45,
                        "memory": {"limit": "512Mi"},
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    input_path = tmp_path / "input.json"
    input_path.write_text('{"message":"hello"}', encoding="utf-8")
    db_path = tmp_path / "goblin.sqlite3"

    result = runner.invoke(
        app,
        [
            "jobs",
            "submit",
            "project.echo",
            "--project",
            str(project_path),
            "--input",
            str(input_path),
            "--runtime",
            "in-process",
            "--db",
            str(db_path),
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["effective_policy"]["memory"]["limit"] == "512Mi"
    job = SQLiteStore(db_path).list_jobs()[0]
    assert job.timeout_seconds == 45
    assert job.metadata["resource_policy"]["memory"]["limit"] == "512Mi"


def test_jobs_submit_applies_layered_goblin_resource_overrides(tmp_path: Path) -> None:
    """Verify per-goblin policy overrides project defaults in persisted policy."""
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(
        json.dumps(
            {
                "goblins": [
                    {
                        "kind": "project.heavy",
                        "display_name": "Project Heavy",
                        "module": "examples.goblins.echo",
                        "entrypoint": "run",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    project_path = tmp_path / "goblin-king-project.json"
    project_path.write_text(
        json.dumps(
            {
                "registries": ["registry.json"],
                "entry_points": False,
                "defaults": {
                    "resources": {
                        "timeout_seconds": 45,
                        "memory": {"limit": "512Mi"},
                        "filesystem": {"artifact_max_files": 2},
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    policies_path = tmp_path / "goblin-resource-policies.json"
    policies_path.write_text(
        json.dumps(
            {
                "version": 1,
                "goblins": {
                    "project.heavy": {
                        "timeout_seconds": 90,
                        "memory": {"limit": "1Gi"},
                    }
                },
                "ceilings": {
                    "timeout_seconds": 120,
                    "memory": {"limit": "2Gi"},
                },
            }
        ),
        encoding="utf-8",
    )
    input_path = tmp_path / "input.json"
    input_path.write_text('{"message":"hello"}', encoding="utf-8")
    db_path = tmp_path / "goblin.sqlite3"

    result = runner.invoke(
        app,
        [
            "jobs",
            "submit",
            "project.heavy",
            "--project",
            str(project_path),
            "--input",
            str(input_path),
            "--runtime",
            "in-process",
            "--db",
            str(db_path),
            "--resource-policies",
            str(policies_path),
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["effective_policy"]["timeout_seconds"] == 90
    assert payload["effective_policy"]["memory"]["limit"] == "1Gi"
    assert payload["effective_policy"]["filesystem"]["artifact_max_files"] == 2
    job = SQLiteStore(db_path).list_jobs()[0]
    assert job.timeout_seconds == 90
    assert job.metadata["resource_policy"]["memory"]["limit"] == "1Gi"


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


def test_deploy_promotion_and_helm_record_commands(tmp_path: Path) -> None:
    """Verify CLI deployment proof commands persist image and Helm records."""
    db_path = tmp_path / "goblin.sqlite3"
    planned = runner.invoke(
        app,
        [
            "deploy",
            "promotions",
            "plan",
            "example.hello",
            "--target-image",
            "registry.example/example-hello:prod",
            "--db",
            str(db_path),
            "--images",
            "goblin-images.json",
            "--build",
            "--push",
        ],
    )
    records = runner.invoke(app, ["deploy", "promotions", "list", "--db", str(db_path)])
    promotion_id = json.loads(planned.stdout)["id"]
    marked = runner.invoke(
        app,
        [
            "deploy",
            "promotions",
            "mark",
            promotion_id,
            "--status",
            "promoted",
            "--digest",
            "sha256:abc",
            "--db",
            str(db_path),
        ],
    )
    helm = runner.invoke(
        app,
        [
            "deploy",
            "helm-template",
            "--db",
            str(db_path),
            "--chart",
            "charts/goblin-king",
        ],
    )
    deployment_records = runner.invoke(app, ["deploy", "records", "--db", str(db_path)])

    assert planned.exit_code == 0
    assert "registry.example/example-hello:prod" in records.stdout
    assert marked.exit_code == 0
    assert json.loads(marked.stdout)["status"] == "promoted"
    assert helm.exit_code == 0
    assert "helm-template" in deployment_records.stdout


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
    assert (tmp_path / "generated" / "workers" / "sample.echo.long-service" / "Dockerfile").exists()


def test_project_init_creates_adopter_template(tmp_path: Path) -> None:
    """Verify the adopter project template generator is reachable from the CLI."""
    result = runner.invoke(
        app,
        [
            "project",
            "init",
            str(tmp_path / "adopter"),
            "--prefix",
            "acme",
        ],
    )

    assert result.exit_code == 0
    assert (tmp_path / "adopter" / "goblin-king-project.json").exists()
    assert (tmp_path / "adopter" / "workers" / "acme.hello" / "Dockerfile").exists()
    assert (tmp_path / "adopter" / "workers" / "acme.artifact" / "Dockerfile").exists()


def test_project_validate_rejects_missing_worker_mapping(tmp_path: Path) -> None:
    """Verify project validation catches missing worker image coverage."""
    project = tmp_path / "project"
    project.mkdir()
    (project / "goblins.json").write_text(
        json.dumps(
            {
                "goblins": [
                    {
                        "kind": "sample.echo",
                        "display_name": "Sample Echo",
                        "module": "examples.goblins.echo",
                        "entrypoint": "run",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (project / "goblin-images.json").write_text('{"workers":{}}', encoding="utf-8")
    (project / "goblin-king-project.json").write_text(
        json.dumps(
            {
                "registries": ["goblins.json"],
                "entry_points": False,
                "images": "goblin-images.json",
                "api_settings": "api.json",
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        ["project", "validate", "--project", str(project / "goblin-king-project.json")],
    )

    assert result.exit_code == 1
    assert "missing_worker\tsample.echo" in result.stderr


def test_scheduler_run_once_uses_project_settings(tmp_path: Path) -> None:
    """Verify scheduler commands can load merged project discovery settings."""
    db_path = tmp_path / "goblin.sqlite3"
    store = SQLiteStore(db_path)
    now = utc_now()
    store.save_schedule(
        ScheduleRecord(
            id="project-schedule",
            kind="project.maintenance.hello",
            input={"name": "Project"},
            cron="* * * * *",
            created_at=now,
            next_run_at=now,
        )
    )

    result = runner.invoke(
        app,
        [
            "scheduler",
            "run-once",
            "--project",
            "examples/adopting-project/goblin-king-project.json",
            "--runtime",
            "in-process",
            "--db",
            str(db_path),
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload[0]["kind"] == "project.maintenance.hello"
    assert payload[0]["status"] == "completed"


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


def test_jobs_submit_accepts_project_settings_and_persists_source_metadata(
    tmp_path: Path,
) -> None:
    """Verify project-defined goblins can be submitted and inspected from CLI."""
    db_path = tmp_path / "goblin.sqlite3"
    submit = runner.invoke(
        app,
        [
            "jobs",
            "submit",
            "project.inline.hello",
            "--input",
            "examples/input.json",
            "--project",
            "examples/adopting-project/goblin-king-project.json",
            "--db",
            str(db_path),
            "--runtime",
            "in-process",
        ],
    )

    assert submit.exit_code == 1
    payload = json.loads(submit.stdout)
    job = SQLiteStore(db_path).get_job(payload["job_id"])
    detail = runner.invoke(
        app,
        ["runs", "show", payload["id"], "--db", str(db_path), "--with-job"],
    )

    assert job is not None
    assert job.metadata["goblin_source"] == "project-config"
    assert job.metadata["goblin_definition"]["kind"] == "project.inline.hello"
    assert detail.exit_code == 0
    assert json.loads(detail.stdout)["goblin_source"] == "project-config"


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


def test_schedules_add_accepts_project_settings(tmp_path: Path) -> None:
    """Verify schedules can be created for project-defined goblins."""
    db_path = tmp_path / "goblin.sqlite3"
    add = runner.invoke(
        app,
        [
            "schedules",
            "add",
            "project.inline.hello",
            "--cron",
            "* * * * *",
            "--input",
            "examples/input.json",
            "--project",
            "examples/adopting-project/goblin-king-project.json",
            "--db",
            str(db_path),
            "--due-now",
        ],
    )

    assert add.exit_code == 0
    assert json.loads(add.stdout)["kind"] == "project.inline.hello"


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


def test_event_stream_status_command(monkeypatch) -> None:
    """Verify CLI operators can inspect Redis Stream delivery status."""

    class FakeRedis:
        def xinfo_stream(self, _stream: str) -> dict:
            return {"length": 5, "last-generated-id": b"5-0"}

        def xinfo_groups(self, _stream: str) -> list[dict]:
            return [{b"name": b"operators", b"pending": 2}]

    monkeypatch.setattr("goblin_king.events.Redis.from_url", lambda _url: FakeRedis())

    result = runner.invoke(app, ["events", "stream-status"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["stream"] == "goblin-king:events:stream"
    assert payload["length"] == 5
    assert payload["pending"] == 2
