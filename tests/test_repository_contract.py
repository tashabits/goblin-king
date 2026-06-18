from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from goblin_king.contracts import RepositoryEntryRecord, RepositoryVersionRecord, utc_now
from goblin_king.store import SQLiteStore


def _repository_entry(
    *,
    entry_id: str = "entry-1",
    name: str = "shared.hello",
    kind: str = "repository.shared.hello",
    project_id: str | None = "default",
    entry_type: str = "notebook_function",
    status: str = "draft",
    published_version: int | None = None,
) -> RepositoryEntryRecord:
    now = utc_now()
    return RepositoryEntryRecord(
        id=entry_id,
        name=name,
        kind=kind,
        type=entry_type,
        project_id=project_id,
        owner="bob",
        display_name="Shared Hello",
        description="A reusable hello goblin",
        tags=["Demo", "hello", "hello"],
        status=status,
        published_version=published_version,
        created_at=now,
        updated_at=now,
    )


def _repository_version(
    *,
    version_id: str = "entry-1-v1",
    entry_id: str = "entry-1",
    version: int = 1,
    source_hash: str = "sha256:one",
    status: str = "draft",
    approval_status: str = "draft",
    approved_by: str | None = None,
    published: bool = False,
) -> RepositoryVersionRecord:
    now = utc_now()
    return RepositoryVersionRecord(
        id=version_id,
        entry_id=entry_id,
        version=version,
        source_hash=source_hash,
        runner_image="goblin-king-notebook-python-function:local",
        validation_proof={"status": "passed", "validator": "test"} if status != "draft" else {},
        approval_status=approval_status,
        status=status,
        approved_by=approved_by,
        approved_at=now if approved_by else None,
        published_at=now if published else None,
        created_at=now,
        updated_at=now,
    )


def test_repository_records_validate_status_type_and_identifiers() -> None:
    entry = _repository_entry()
    version = _repository_version()

    assert entry.name == "shared.hello"
    assert entry.kind == "repository.shared.hello"
    assert entry.type == "notebook_function"
    assert entry.tags == ["demo", "hello"]
    assert version.status == "draft"

    for status in (
        "draft",
        "validated",
        "pending_review",
        "approved",
        "published",
        "rejected",
        "retired",
    ):
        assert _repository_entry(entry_id=f"entry-{status}", status=status).status == status
        assert _repository_version(version_id=f"version-{status}", status=status).status == status

    assert _repository_entry(entry_type="notebook_service").type == "notebook_service"

    with pytest.raises(ValidationError):
        _repository_entry(name="Shared Hello")

    with pytest.raises(ValidationError):
        _repository_entry(kind="repository/hello")

    with pytest.raises(ValidationError):
        _repository_entry(status="archived")

    with pytest.raises(ValidationError):
        _repository_entry(entry_type="raw_pod")


def test_store_creates_lists_and_loads_repository_entries_and_versions(
    tmp_path: Path,
) -> None:
    store = SQLiteStore(tmp_path / "goblin.sqlite3")
    entry = store.create_repository_entry(_repository_entry())
    entry = store.update_repository_entry(
        entry.model_copy(
            update={
                "display_name": "Shared Hello v1",
                "description": "Updated repository entry.",
                "tags": ["updated", "hello"],
                "updated_at": utc_now(),
            }
        )
    )
    first = store.create_repository_version(_repository_version())
    second = store.create_repository_version(
        _repository_version(
            version_id="entry-1-v2",
            version=2,
            source_hash="sha256:two",
        )
    )

    assert entry.display_name == "Shared Hello v1"
    assert entry.tags == ["updated", "hello"]
    assert first.version == 1
    assert second.version == 2
    loaded_entry = store.get_repository_entry("entry-1")
    assert loaded_entry is not None
    assert loaded_entry.display_name == entry.display_name
    assert loaded_entry.tags == entry.tags
    assert loaded_entry.status == "draft"
    assert store.get_repository_entry_by_project_name("default", "shared.hello") == loaded_entry
    assert store.get_repository_version("entry-1", 1) == first
    assert (
        store.get_repository_version_by_project_name("default", "shared.hello", 2)
        == second
    )
    assert store.list_repository_entries(project_id="default") == [loaded_entry]
    assert store.list_repository_entries(entry_type="notebook_function") == [loaded_entry]
    assert store.list_repository_versions("entry-1") == [first, second]


def test_store_enforces_active_project_name_uniqueness_and_version_sequence(
    tmp_path: Path,
) -> None:
    store = SQLiteStore(tmp_path / "goblin.sqlite3")
    store.create_repository_entry(_repository_entry())
    store.create_repository_version(_repository_version())

    with pytest.raises(ValueError, match="name already exists"):
        store.create_repository_entry(
            _repository_entry(entry_id="entry-duplicate", kind="repository.duplicate")
        )

    other_project = store.create_repository_entry(
        _repository_entry(
            entry_id="entry-other-project",
            kind="repository.shared.hello.other",
            project_id="other",
        )
    )
    assert other_project.project_id == "other"
    assert other_project.name == "shared.hello"

    with pytest.raises(ValueError, match="next sequential version"):
        store.create_repository_version(
            _repository_version(
                version_id="entry-1-v3",
                version=3,
                source_hash="sha256:three",
            )
        )

    with pytest.raises(ValueError, match="source hash must change"):
        store.create_repository_version(
            _repository_version(
                version_id="entry-1-v2-same-source",
                version=2,
                source_hash="sha256:one",
            )
        )


def test_store_transitions_repository_status_through_publication(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "goblin.sqlite3")
    entry = store.create_repository_entry(_repository_entry())
    store.create_repository_version(_repository_version())
    now = utc_now()

    validated = store.transition_repository_version_status(
        entry.id,
        1,
        "validated",
        updated_at=now,
        validation_proof={"status": "passed"},
    )
    pending = store.transition_repository_version_status(
        entry.id,
        1,
        "pending_review",
        updated_at=now,
    )
    approved = store.transition_repository_version_status(
        entry.id,
        1,
        "approved",
        updated_at=now,
        approved_by="alice",
    )
    published = store.transition_repository_version_status(
        entry.id,
        1,
        "published",
        updated_at=now,
    )
    published_entry = store.get_repository_entry(entry.id)

    assert validated.status == "validated"
    assert validated.validation_proof == {"status": "passed"}
    assert pending.status == "pending_review"
    assert approved.status == "approved"
    assert approved.approved_by == "alice"
    assert approved.approved_at is not None
    assert published.status == "published"
    assert published.published_at is not None
    assert published_entry is not None
    assert published_entry.status == "published"
    assert published_entry.published_version == 1

    with pytest.raises(ValueError, match="published repository versions are immutable"):
        store.transition_repository_version_status(entry.id, 1, "draft", updated_at=now)


def test_source_change_requires_new_draft_version_without_approval(
    tmp_path: Path,
) -> None:
    store = SQLiteStore(tmp_path / "goblin.sqlite3")
    entry = store.create_repository_entry(_repository_entry())
    store.create_repository_version(_repository_version())
    now = utc_now()
    store.transition_repository_version_status(
        entry.id,
        1,
        "validated",
        updated_at=now,
        validation_proof={"status": "passed"},
    )
    store.transition_repository_version_status(entry.id, 1, "pending_review", updated_at=now)
    store.transition_repository_version_status(
        entry.id,
        1,
        "approved",
        updated_at=now,
        approved_by="alice",
    )

    with pytest.raises(ValueError, match="must start as drafts"):
        store.create_repository_version(
            _repository_version(
                version_id="entry-1-v2-approved",
                version=2,
                source_hash="sha256:two",
                status="approved",
                approval_status="approved",
                approved_by="alice",
            )
        )

    draft = store.create_repository_version(
        _repository_version(
            version_id="entry-1-v2",
            version=2,
            source_hash="sha256:two",
        )
    )

    assert draft.status == "draft"
    assert draft.approval_status == "draft"
    assert draft.validation_proof == {}
    assert draft.approved_by is None
    assert draft.approved_at is None
    assert draft.published_at is None


def test_published_repository_versions_keep_source_identity_immutable(
    tmp_path: Path,
) -> None:
    store = SQLiteStore(tmp_path / "goblin.sqlite3")
    entry = store.create_repository_entry(_repository_entry())
    store.create_repository_version(_repository_version())
    now = utc_now()
    store.transition_repository_version_status(
        entry.id,
        1,
        "validated",
        updated_at=now,
        validation_proof={"status": "passed"},
    )
    store.transition_repository_version_status(entry.id, 1, "pending_review", updated_at=now)
    store.transition_repository_version_status(
        entry.id,
        1,
        "approved",
        updated_at=now,
        approved_by="alice",
    )
    published = store.transition_repository_version_status(
        entry.id,
        1,
        "published",
        updated_at=now,
    )

    with pytest.raises(ValueError, match="published repository versions are immutable"):
        store.update_repository_version(
            published.model_copy(update={"source_hash": "sha256:changed"})
        )

    with pytest.raises(ValueError, match="published repository versions are immutable"):
        store.update_repository_version(published.model_copy(update={"version": 2}))

    loaded = store.get_repository_version(entry.id, 1)
    assert loaded is not None
    assert loaded.version == 1
    assert loaded.source_hash == "sha256:one"

    retired_entry = store.transition_repository_entry_status(
        entry.id,
        "retired",
        updated_at=now,
    )

    assert retired_entry.status == "retired"
    assert retired_entry.published_version == 1
    assert store.get_repository_entry_by_project_name("default", "shared.hello") is None
    assert (
        store.get_repository_entry_by_project_name(
            "default",
            "shared.hello",
            include_retired=True,
        )
        == retired_entry
    )
    assert store.get_repository_version(entry.id, 1) == published
