"""Tests for notebook-defined Python function goblins."""

from __future__ import annotations

from pathlib import Path

from goblin_king.contracts import GoblinResult, JobRecord, NotebookGoblinRecord, utc_now
from goblin_king.notebooks import (
    GoblinKingNotebookClient,
    notebook_source_hash,
    notebook_worker_input,
)
from goblin_king.registry import GoblinRegistry
from goblin_king.runtime import DockerRuntime
from goblin_king.scheduler import Scheduler
from goblin_king.store import SQLiteStore
from goblin_king.validation import WorkerValidationResult
from goblin_king.workers import WorkerImageMap
from tests.api_helpers import auth_headers, build_api_client


def test_notebook_client_declare_posts_function_source(monkeypatch) -> None:
    """Verify notebook users can declare a function through the public helper."""
    requests = []

    def hello(payload):
        return {"message": payload["name"]}

    def fake_request(self, method, path, payload=None):
        requests.append((method, path, payload))
        return {
            "kind": payload["kind"],
            "source_hash": notebook_source_hash(payload["source"], payload["function_name"]),
        }

    monkeypatch.setenv("GOBLIN_KING_API_TOKEN", "token")
    monkeypatch.setattr(GoblinKingNotebookClient, "_request", fake_request)

    client = GoblinKingNotebookClient(api_url="http://goblin.local")
    declared = client.declare(hello, kind="notebook.hello")

    assert declared.kind == "notebook.hello"
    assert requests[0][0] == "POST"
    assert requests[0][1] == "/notebooks/goblins"
    assert "def hello(payload):" in requests[0][2]["source"]
    assert requests[0][2]["function_name"] == "hello"


def test_api_builds_lists_and_submits_notebook_goblin(tmp_path: Path) -> None:
    """Verify the API stores notebook function bundles and submits them by custom kind."""
    client, store, _artifact_root = build_api_client(tmp_path)
    source = "def run(payload):\n    return {'message': payload['name']}\n"

    built = client.post(
        "/notebooks/goblins",
        headers=auth_headers(),
        json={
            "kind": "notebook.hello",
            "display_name": "Notebook Hello",
            "source": source,
            "function_name": "run",
            "timeout_seconds": 20,
        },
    )

    assert built.status_code == 200
    assert built.json()["source_hash"] == notebook_source_hash(source, "run")
    assert store.get_notebook_goblin("notebook.hello") is not None

    listed = client.get("/goblins", headers=auth_headers())
    notebook_entry = [item for item in listed.json() if item["kind"] == "notebook.hello"][0]
    assert notebook_entry["source"] == "notebook"
    assert notebook_entry["worker_image"] == "goblin-king-notebook-python-function:local"

    job = client.post(
        "/jobs",
        headers=auth_headers(),
        json={"kind": "notebook.hello", "input": {"name": "Ada"}},
    )

    assert job.status_code == 200
    assert job.json()["kind"] == "notebook.hello"
    assert job.json()["input"] == {"name": "Ada"}
    assert job.json()["metadata"]["goblin_source"] == "notebook"


def test_api_validates_notebook_goblin_with_wrapped_input(tmp_path: Path, monkeypatch) -> None:
    """Verify validation receives the stored function source plus user payload."""
    client, _store, _artifact_root = build_api_client(tmp_path)
    source = "def run(payload):\n    return {'message': payload['name']}\n"
    client.post(
        "/notebooks/goblins",
        headers=auth_headers(),
        json={"kind": "notebook.hello", "source": source, "function_name": "run"},
    )
    seen = {}

    def fake_validate_workers(**kwargs):
        seen.update(kwargs)
        return [
            WorkerValidationResult(
                kind="notebook.hello",
                ok=True,
                image="goblin-king-notebook-python-function:local",
                image_digest="sha256:runner",
            )
        ]

    monkeypatch.setattr("goblin_king.api.validate_workers", fake_validate_workers)

    response = client.post(
        "/notebooks/goblins/notebook.hello/validate",
        headers=auth_headers(),
        json={"input": {"name": "Ada"}},
    )

    assert response.status_code == 200
    assert response.json()["validation"]["ok"] is True
    assert seen["input_payload"]["payload"] == {"name": "Ada"}
    assert seen["input_payload"]["source"] == source
    assert seen["input_payload"]["function"] == "run"


def test_scheduler_runs_notebook_goblin_with_stored_bundle(tmp_path: Path, monkeypatch) -> None:
    """Verify dynamic notebook kinds are resolved from the store before Docker execution."""
    store = SQLiteStore(tmp_path / "goblin.sqlite3")
    record = NotebookGoblinRecord(
        kind="notebook.hello",
        display_name="Notebook Hello",
        image="goblin-king-notebook-python-function:local",
        source="def run(payload):\n    return {'message': payload['name']}\n",
        source_hash="sha256-source",
        function_name="run",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    store.save_notebook_goblin(record)
    job = JobRecord(
        id="job-1",
        kind="notebook.hello",
        input={"name": "Ada"},
        created_at=utc_now(),
        status="leased",
        lease_owner="test-worker",
    )
    store.save_job(job)
    scheduler = Scheduler(
        registry=GoblinRegistry.from_definitions([]),
        store=store,
        worker_id="test-worker",
        runtime_mode="docker",
        workers=WorkerImageMap.from_definitions({}, root=tmp_path),
    )
    captured = {}

    monkeypatch.setattr(
        "goblin_king.scheduler.inspect_image_identity",
        lambda _docker, _image: ("sha256:runner", None),
    )
    monkeypatch.setattr(
        "goblin_king.scheduler.validate_workers",
        lambda **kwargs: [
            WorkerValidationResult(
                kind="notebook.hello",
                ok=True,
                image="goblin-king-notebook-python-function:local",
                image_digest="sha256:runner",
            )
        ],
    )

    def fake_run(self, definition, _entrypoint, input_payload, context, **_kwargs):
        captured["definition"] = definition
        captured["input_payload"] = input_payload
        captured["context"] = context
        return GoblinResult.ok(data={"message": input_payload["payload"]["name"]})

    monkeypatch.setattr(DockerRuntime, "run", fake_run)

    run = scheduler.run_claimed_job(job)

    assert run.status == "completed"
    assert run.result and run.result.data == {"message": "Ada"}
    assert captured["definition"].kind == "notebook.hello"
    assert captured["input_payload"] == notebook_worker_input(record, {"name": "Ada"})
