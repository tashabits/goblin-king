"""Tests for the human-facing demo and doctor onboarding commands."""

from __future__ import annotations

from pathlib import Path
from urllib.error import URLError

from redis.exceptions import RedisError
from typer.testing import CliRunner

from goblin_king.cli import app
from goblin_king.demo import (
    CompletedCommand,
    DemoResult,
    compose_command,
    run_demo_up,
)
from goblin_king.doctor import DoctorCheck, DoctorResult, run_doctor
from goblin_king.store import SQLiteStore
from goblin_king.templates import init_project
from goblin_king.validation import WorkerValidationResult

runner = CliRunner()


class FakeCommandRunner:
    """Capture subprocess-style commands without touching Docker."""

    def __init__(self, *, returncode: int = 0) -> None:
        self.returncode = returncode
        self.commands: list[list[str]] = []
        self.envs: list[dict[str, str] | None] = []

    def run(
        self,
        args: list[str],
        *,
        env: dict[str, str] | None = None,
        timeout_seconds: int | None = None,
    ) -> CompletedCommand:
        self.commands.append(args)
        self.envs.append(env)
        return CompletedCommand(
            args=args,
            returncode=self.returncode,
            stdout="ok",
            stderr="" if self.returncode == 0 else "failed",
        )


class FakeDemoHttpClient:
    """Return deterministic admin/API payloads for the demo proof flow."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, str]] = []

    def request_json(
        self,
        method: str,
        url: str,
        *,
        token: str | None = None,
        payload: dict | None = None,
        timeout_seconds: int = 10,
    ) -> dict:
        self.requests.append((method, url))
        if url.endswith("/health"):
            return {"ok": "true"}
        if url.endswith("/admin/discovery/reload"):
            return {"active_goblin_count": 1, "discovery_version": "test-version"}
        if url.endswith("/jobs") and method == "POST":
            return {"id": "job-1", "kind": payload["kind"], "status": "queued"}
        if url.endswith("/jobs/job-1"):
            return {"id": "job-1", "kind": "demo.hello", "status": "completed"}
        if "/runs?" in url:
            return {
                "items": [
                    {
                        "id": "run-1",
                        "job_id": "job-1",
                        "kind": "demo.hello",
                        "status": "completed",
                        "result": {"status": "success", "artifacts": []},
                    }
                ],
                "meta": {"count": 1},
            }
        raise AssertionError(f"unexpected request: {method} {url}")


class UnavailableHttpClient:
    """Simulate a local admin stack that has not been started yet."""

    def request_json(
        self,
        method: str,
        url: str,
        *,
        token: str | None = None,
        payload: dict | None = None,
        timeout_seconds: int = 10,
    ) -> dict:
        raise URLError("connection refused")


def test_demo_up_invokes_compose_validates_and_submits_job(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Verify demo up performs Compose, validation, discovery, and job proof steps."""
    project_dir = init_project(tmp_path / "project", prefix="demo")
    project = project_dir / "goblin-king-project.json"
    fake_runner = FakeCommandRunner()
    fake_http = FakeDemoHttpClient()

    def fake_validate_workers(**kwargs):
        return [
            WorkerValidationResult(
                kind=kwargs["kinds"][0],
                ok=True,
                image="demo-hello:local",
                image_digest="sha256:demo",
                result_status="success",
                checks=["context", "dockerfile", "build", "result-envelope"],
            )
        ]

    monkeypatch.setattr("goblin_king.demo.validate_workers", fake_validate_workers)

    result = run_demo_up(
        project=project,
        kind="demo.hello",
        input_path=project_dir / "inputs" / "hello.json",
        timeout_seconds=1,
        poll_seconds=0,
        command_runner=fake_runner,
        http_client=fake_http,
    )

    assert result.ok is True
    assert result.job["id"] == "job-1"
    assert result.run["id"] == "run-1"
    assert fake_runner.commands == [
        compose_command(
            [
                "up",
                "-d",
                "--build",
                "redis",
                "api",
                "admin",
                "scheduler",
                "long-hello",
                "worker-project-maintenance-hello",
                "worker-project-reports-long-service",
            ]
        )
    ]
    assert fake_runner.envs[0]["HOST_PROJECT_PATH"] == str(project_dir.resolve())
    store = SQLiteStore(project_dir / ".goblin-king" / "goblin-king.sqlite3")
    records = store.list_worker_validations(kind="demo.hello")
    assert records[0].status == "passed"
    assert ("POST", "http://127.0.0.1:8080/admin-api/jobs") in fake_http.requests


def test_demo_up_cli_json(monkeypatch) -> None:
    """Verify demo up exposes stable machine-readable proof."""
    monkeypatch.setattr(
        "goblin_king.cli.run_demo_up",
        lambda **_kwargs: DemoResult(
            ok=True,
            stage="complete",
            admin_url="http://127.0.0.1:8080/admin",
            api_base_url="http://127.0.0.1:8080/admin-api",
            project="examples/adopting-project/goblin-king-project.json",
            kind="project.inline.hello",
            input="examples/input.json",
            cleanup="goblin-king demo down",
            job={"id": "job-1", "status": "completed"},
            run={"id": "run-1", "status": "completed"},
        ),
    )

    result = runner.invoke(app, ["demo", "up", "--json"])

    assert result.exit_code == 0
    assert '"ok": true' in result.stdout
    assert '"admin_url": "http://127.0.0.1:8080/admin"' in result.stdout
    assert '"run": {' in result.stdout


def test_doctor_reports_actionable_warnings_for_stack_not_running(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Verify doctor can diagnose a not-yet-started stack without failing hard."""
    project_dir = init_project(tmp_path / "project", prefix="demo")
    fake_runner = FakeCommandRunner()

    class FakeRedis:
        @classmethod
        def from_url(cls, _url):
            raise RedisError("redis down")

    monkeypatch.setattr("goblin_king.doctor.Redis", FakeRedis)

    result = run_doctor(
        project=project_dir / "goblin-king-project.json",
        kind="demo.hello",
        command_runner=fake_runner,
        http_client=UnavailableHttpClient(),
    )

    assert result.ok is True
    checks = {check.name: check for check in result.checks}
    assert checks["docker_daemon"].status == "pass"
    assert checks["admin_api"].status == "warn"
    assert checks["admin_api"].repair_command == "goblin-king demo up"
    assert checks["validation_status"].status == "warn"
    assert "workers validate" in checks["validation_status"].repair_command


def test_doctor_reports_missing_docker_as_failure(monkeypatch) -> None:
    """Verify missing Docker is a hard prerequisite failure."""
    monkeypatch.setattr("goblin_king.doctor.shutil.which", lambda _name: None)

    result = run_doctor(http_client=UnavailableHttpClient())

    checks = {check.name: check for check in result.checks}
    assert result.ok is False
    assert checks["docker_cli"].status == "fail"
    assert "Install Docker" in checks["docker_cli"].repair_command


def test_doctor_cli_json(monkeypatch) -> None:
    """Verify doctor emits stable JSON diagnostics."""
    monkeypatch.setattr(
        "goblin_king.cli.run_doctor",
        lambda **_kwargs: DoctorResult(
            ok=True,
            project="examples/adopting-project/goblin-king-project.json",
            kind="project.inline.hello",
            admin_url="http://127.0.0.1:8080/admin",
            checks=[
                DoctorCheck(
                    name="python_package",
                    status="pass",
                    message="Goblin King imported successfully.",
                )
            ],
        ),
    )

    result = runner.invoke(app, ["doctor", "--json"])

    assert result.exit_code == 0
    assert '"ok": true' in result.stdout
    assert '"name": "python_package"' in result.stdout
