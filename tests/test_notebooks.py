"""Tests for notebook-defined Python function goblins."""

from __future__ import annotations

import json
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


def test_branch_workbook_is_valid_and_branch_pinned() -> None:
    """Verify the uploadable branch workbook installs the PR branch explicitly."""
    path = Path("examples/jupyterhub-goblin-king/workbook-launch-branch.ipynb")
    workbook = json.loads(path.read_text(encoding="utf-8"))
    source = "\n".join(
        "".join(cell.get("source", []))
        for cell in workbook["cells"]
        if cell.get("cell_type") == "code"
    )

    branch_package = (
        "git+https://github.com/tashabits/goblin-king.git@service-workloads-jupyterhub-auth"
    )

    assert branch_package in source
    assert "--force-reinstall" in source
    assert "--no-deps" in source
    assert "site.getusersitepackages()" in source
    assert 'module_name == "goblin_king"' in source
    assert 'module_name.startswith("goblin_king.")' in source
    assert "Path(goblin_king.__file__).resolve()" in source
    assert "JUPYTERHUB_API_TOKEN is required" in source
    assert "from goblin_king.notebooks import GoblinKingNotebookClient" in source
    assert "progress=True" in source
    assert "/services/long-running/{service['id']}/stop" in source


def test_default_workbook_uses_progress_without_branch_pin() -> None:
    """Verify the stable workbook stays default-branch oriented but shows progress."""
    path = Path("examples/jupyterhub-goblin-king/workbook-launch.ipynb")
    workbook = json.loads(path.read_text(encoding="utf-8"))
    source = "\n".join(
        "".join(cell.get("source", []))
        for cell in workbook["cells"]
        if cell.get("cell_type") == "code"
    )

    branch_package = (
        "git+https://github.com/tashabits/goblin-king.git@service-workloads-jupyterhub-auth"
    )

    assert branch_package not in source
    assert "git+https://github.com/tashabits/goblin-king.git" in source
    assert "--no-deps" in source
    assert "progress=True" in source
    assert "site.getusersitepackages()" in source
    assert 'module_name == "goblin_king"' in source
    assert 'module_name.startswith("goblin_king.")' in source


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


def test_notebook_client_request_uses_configured_timeout(monkeypatch) -> None:
    """Verify notebook HTTP calls use the client-level request timeout."""
    seen = {}

    class FakeResponse:
        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self) -> bytes:
            return json.dumps({"ok": True}).encode("utf-8")

    def fake_urlopen(request, timeout):
        seen["url"] = request.full_url
        seen["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setenv("GOBLIN_KING_API_TOKEN", "token")
    monkeypatch.setattr("goblin_king.notebooks.urlrequest.urlopen", fake_urlopen)

    client = GoblinKingNotebookClient(
        api_url="http://goblin.local",
        request_timeout_seconds=321,
    )
    assert client._request("GET", "/health") == {"ok": True}

    assert seen == {"url": "http://goblin.local/health", "timeout": 321}


def test_notebook_client_run_is_silent_by_default(monkeypatch, capsys) -> None:
    """Verify existing notebook run behavior remains quiet unless progress is requested."""
    client = GoblinKingNotebookClient(api_url="http://goblin.local", token="token")

    def fake_request(_method, path, _payload=None):
        if path == "/jobs":
            return {"id": "job-1", "status": "queued"}
        if path == "/jobs/job-1":
            return {"id": "job-1", "status": "completed"}
        if path.startswith("/runs"):
            return {"items": [{"id": "run-1", "job_id": "job-1", "status": "completed"}]}
        raise AssertionError(path)

    monkeypatch.setattr(client, "_request", fake_request)

    result = client.run("notebook.hello", {}, poll_seconds=0)

    assert result["run"]["id"] == "run-1"
    assert capsys.readouterr().out == ""


def test_notebook_client_run_prints_progress(monkeypatch, capsys) -> None:
    """Verify opt-in notebook progress prints compact polling updates."""
    client = GoblinKingNotebookClient(api_url="http://goblin.local", token="token")
    job_statuses = iter(["leased", "completed"])
    current_status = {"value": "queued"}

    def fake_request(_method, path, _payload=None):
        if path == "/jobs":
            return {"id": "job-1", "status": "queued"}
        if path == "/jobs/job-1":
            current_status["value"] = next(job_statuses)
            return {"id": "job-1", "status": current_status["value"]}
        if path.startswith("/runs"):
            run_status = "completed" if current_status["value"] == "completed" else "running"
            return {"items": [{"id": "run-1", "job_id": "job-1", "status": run_status}]}
        raise AssertionError(path)

    monkeypatch.setattr(client, "_request", fake_request)

    client.run(
        "notebook.hello",
        {},
        poll_seconds=0,
        progress=True,
        progress_interval_seconds=0,
    )

    output = capsys.readouterr().out
    assert "notebook.hello job=queued run=none" in output
    assert "notebook.hello job=leased run=running" in output
    assert "notebook.hello job=completed run=completed" in output


def test_notebook_client_run_progress_callback_does_not_print(monkeypatch, capsys) -> None:
    """Verify callbacks receive progress payloads without enabling printed output."""
    client = GoblinKingNotebookClient(api_url="http://goblin.local", token="token")
    job_statuses = iter(["leased", "completed"])
    current_status = {"value": "queued"}
    events = []

    def fake_request(_method, path, _payload=None):
        if path == "/jobs":
            return {"id": "job-1", "status": "queued"}
        if path == "/jobs/job-1":
            current_status["value"] = next(job_statuses)
            return {"id": "job-1", "status": current_status["value"]}
        if path.startswith("/runs"):
            run_status = "completed" if current_status["value"] == "completed" else "running"
            return {"items": [{"id": "run-1", "job_id": "job-1", "status": run_status}]}
        raise AssertionError(path)

    monkeypatch.setattr(client, "_request", fake_request)

    client.run(
        "notebook.hello",
        {},
        poll_seconds=0,
        progress_interval_seconds=0,
        on_progress=events.append,
    )

    assert capsys.readouterr().out == ""
    assert [event["phase"] for event in events] == ["submitted", "polling", "completed"]
    assert events[-1] == {
        "phase": "completed",
        "kind": "notebook.hello",
        "job_id": "job-1",
        "job_status": "completed",
        "run_id": "run-1",
        "run_status": "completed",
        "elapsed_seconds": 0.0,
    }


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
