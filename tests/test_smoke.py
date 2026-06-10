"""Tests for local adopter smoke proof commands."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from typer.testing import CliRunner

from goblin_king.cli import app
from goblin_king.contracts import GoblinResult, RunRecord
from goblin_king.smoke import AdopterSmokeResult, run_adopter_project_smoke
from goblin_king.validation import WorkerValidationResult

runner = CliRunner()


def test_adopter_project_smoke_summarizes_success_artifact_and_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Verify the smoke flow proves success, artifact, failure, and cleanup."""

    def fake_validate_workers(**kwargs):
        kind = kwargs["kinds"][0]
        return [
            WorkerValidationResult(
                kind=kind,
                ok=True,
                image=f"{kind}:local",
                result_status="failed" if kind.endswith(".failure") else "success",
                checks=["context", "dockerfile", "build", "result-envelope"],
                artifact_count=1 if kind.endswith(".artifact") else 0,
            )
        ]

    class FakeScheduler:
        def __init__(self, **_kwargs) -> None:
            pass

        def run_once(self, _now):
            started = datetime.now(UTC)
            return [
                RunRecord(
                    id="run-hello",
                    job_id="job-hello",
                    kind="acme.hello",
                    status="completed",
                    started_at=started,
                    finished_at=started,
                    result=GoblinResult.ok(data={"message": "Hello World"}),
                ),
                RunRecord(
                    id="run-artifact",
                    job_id="job-artifact",
                    kind="acme.artifact",
                    status="completed",
                    started_at=started,
                    finished_at=started,
                    result=GoblinResult.ok(
                        data={"message": "artifact written"},
                        artifacts=[
                            {
                                "name": "report.txt",
                                "uri": "artifact://report.txt",
                                "content_type": "text/plain",
                            }
                        ],
                    ),
                ),
                RunRecord(
                    id="run-failure",
                    job_id="job-failure",
                    kind="acme.failure",
                    status="failed",
                    started_at=started,
                    finished_at=started,
                    result=GoblinResult.failed(error="expected adopter smoke failure"),
                    error="expected adopter smoke failure",
                ),
            ]

    monkeypatch.setattr("goblin_king.smoke.validate_workers", fake_validate_workers)
    monkeypatch.setattr("goblin_king.smoke.Scheduler", FakeScheduler)

    result = run_adopter_project_smoke(
        work_dir=tmp_path,
        prefix="acme",
        keep=True,
    )

    assert result.ok is True
    assert result.cleanup == "kept"
    assert result.result_classes == {
        "acme.hello": "completed",
        "acme.artifact": "completed",
        "acme.failure": "failed",
    }
    assert result.artifact_count == 1
    assert result.failure_error == "expected adopter smoke failure"


def test_smoke_adopter_project_cli_prints_json(monkeypatch) -> None:
    """Verify the smoke CLI surfaces structured proof."""

    def fake_smoke(**_kwargs):
        return AdopterSmokeResult(
            ok=True,
            cleanup="removed",
            scheduled_kinds=["smoke.hello", "smoke.artifact", "smoke.failure"],
            result_classes={
                "smoke.hello": "completed",
                "smoke.artifact": "completed",
                "smoke.failure": "failed",
            },
            artifact_count=1,
            failure_error="expected adopter smoke failure",
        )

    monkeypatch.setattr("goblin_king.cli.run_adopter_project_smoke", fake_smoke)

    result = runner.invoke(app, ["smoke", "adopter-project"])

    assert result.exit_code == 0
    assert '"ok": true' in result.stdout
    assert "smoke.failure" in result.stdout
