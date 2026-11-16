"""Prove local durable source admission using generic fixtures; no worker is started."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from goblin_king.pipeline_contracts import PipelineCatalog, PipelineServiceContract, SourceChange
from goblin_king.pipeline_store import PipelineAdmissionStore
from goblin_king.store import SQLiteStore


def main() -> None:
    """Validate fixture contracts and show replay identity surviving store recreation."""
    root = Path(__file__).resolve().parent
    PipelineServiceContract.model_validate_json((root / "service.json").read_text(encoding="utf-8"))
    catalog = PipelineCatalog.model_validate_json(
        (root / "catalog.json").read_text(encoding="utf-8")
    )
    registration = catalog.registrations[0]
    change = SourceChange(
        delivery_id="fixture-delivery-1",
        repository="example/sample-api",
        branch="test",
        commit="c" * 40,
        actor="fixture-maintainer",
    )
    with TemporaryDirectory(prefix="pipeline-admission-") as directory:
        path = Path(directory) / "state.sqlite3"
        original_store = SQLiteStore(path)
        try:
            first = PipelineAdmissionStore(original_store).admit(registration, change)
        finally:
            original_store.engine.dispose()
        restarted_store = SQLiteStore(path)
        try:
            admission = PipelineAdmissionStore(restarted_store)
            replay = admission.admit(registration, change)
            requests = admission.list_requests(project_id=registration.project_id)
            jobs = restarted_store.list_jobs()
            if not first.created or replay.created or first.request != replay.request:
                raise RuntimeError("replay did not preserve the original admitted request")
            if len(requests) != 1 or jobs:
                raise RuntimeError(
                    "admission created duplicate intent or unexpected scheduler work"
                )
            print(
                json.dumps(
                    {
                        "result": "passed",
                        "build_id": first.request.build_id,
                        "source_commit": first.request.source.commit,
                        "requests": len(requests),
                        "scheduler_jobs": len(jobs),
                        "duplicate_created": replay.created,
                        "restart_preserved_identity": True,
                        "cluster_execution": "not exercised",
                    },
                    indent=2,
                )
            )
        finally:
            restarted_store.engine.dispose()


if __name__ == "__main__":
    main()
