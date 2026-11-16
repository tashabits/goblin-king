"""Exercise fixture admission on a real mounted database; never submit worker jobs."""

from __future__ import annotations

import argparse
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from goblin_king.pipeline_contracts import PipelineCatalog, SourceChange
from goblin_king.pipeline_store import PipelineAdmissionStore
from goblin_king.store import SQLiteStore


def probe(path: Path, expected_build_id: str | None = None) -> dict:
    """Seed once or verify existing identity before replay, failing if persistence was lost."""
    catalog = PipelineCatalog.model_validate_json(
        Path(__file__).with_name("catalog.json").read_text(encoding="utf-8")
    )
    registration = catalog.registrations[0]
    change = SourceChange(
        delivery_id="cluster-foundation-fixture",
        repository=registration.source_repository,
        branch="test",
        commit="c" * 40,
        actor="fixture-maintainer",
    )
    store = SQLiteStore(path)
    try:
        admissions = PipelineAdmissionStore(store)
        before = admissions.list_requests(project_id=registration.project_id)
        if expected_build_id and (len(before) != 1 or before[0].build_id != expected_build_id):
            raise RuntimeError("persisted build identity is missing or changed before replay")
        result = admissions.admit(registration, change)
        if expected_build_id and result.created:
            raise RuntimeError("replay created a new request")
        if len(admissions.list_requests(project_id=registration.project_id)) != 1:
            raise RuntimeError("fixture has duplicate requests")
        if store.list_jobs():
            raise RuntimeError("foundation fixture must not dispatch scheduler jobs")
        with closing(sqlite3.connect(path)) as connection:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError("SQLite integrity check failed")
        return {
            "build_id": result.request.build_id,
            "created": result.created,
            "requests": 1,
            "scheduler_jobs": 0,
            "integrity_check": integrity,
        }
    finally:
        store.engine.dispose()


def main() -> None:
    """Optionally create an online SQLite backup and verify a separate restored database."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--expected-build-id")
    parser.add_argument("--backup", type=Path)
    args = parser.parse_args()
    result = probe(args.db, args.expected_build_id)
    if args.backup:
        if args.backup.exists() or args.backup.resolve() == args.db.resolve():
            raise ValueError("backup destination must be a new, distinct file")
        with (
            closing(sqlite3.connect(args.db)) as source,
            closing(sqlite3.connect(args.backup)) as destination,
        ):
            source.backup(destination)
        result["backup"] = probe(args.backup, result["build_id"])
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
