"""Validation identity tests for the versioned Kubernetes security profile."""

from pathlib import Path

from goblin_king.contracts import (
    GoblinDefinition,
    JobRecord,
    WorkerValidationRecord,
    utc_now,
)
from goblin_king.kubernetes_runtime_settings import KubernetesRuntimeSettings
from goblin_king.registry import GoblinRegistry
from goblin_king.scheduler import Scheduler
from goblin_king.store import SQLiteStore
from goblin_king.validation import VALIDATOR_VERSION
from goblin_king.versions import GOBLIN_CONTAINER_CONTRACT_VERSION
from goblin_king.workers import WorkerImageDefinition, WorkerImageMap


def test_restricted_scheduler_requires_security_bound_validation_identity(
    tmp_path: Path,
) -> None:
    kind = "example.secure"
    image = "registry.example/secure@sha256:" + "a" * 64
    settings = KubernetesRuntimeSettings(workload_security_profile="restricted-v1")
    store = SQLiteStore(tmp_path / "scheduler.sqlite3")
    workers = WorkerImageMap(
        {kind: WorkerImageDefinition(context=".", image=image)},
        root=".",
    )
    scheduler = Scheduler(
        registry=GoblinRegistry.from_definitions(
            [GoblinDefinition(kind=kind, display_name="Secure", module="container.only")]
        ),
        store=store,
        runtime_mode="kubernetes",
        workers=workers,
        kubernetes_runtime_settings=settings,
    )
    job = JobRecord(id="job-secure", kind=kind, input={}, created_at=utc_now())

    store.save_worker_validation(
        _proof(
            kind=kind,
            image=image,
            identity=f"kubernetes:{image}",
            effective_policy={},
        )
    )
    rejected = scheduler._validate_before_container_run(
        job,
        kind,
        resource_policy={},
    )

    assert rejected is not None
    assert "no current Kubernetes validation proof exists" in rejected

    identity = settings.validation_image_identity(image, kind)
    effective_security = settings.effective_workload_security(kind)
    store.save_worker_validation(
        _proof(
            kind=kind,
            image=image,
            identity=identity,
            effective_policy={"kubernetes_workload_security": effective_security},
        )
    )

    assert scheduler._validate_before_container_run(
        job,
        kind,
        resource_policy={},
    ) is None
    saved = store.get_latest_worker_validation(
        kind=kind,
        image_digest=identity,
        contract_version=GOBLIN_CONTAINER_CONTRACT_VERSION,
        validator_version=VALIDATOR_VERSION,
    )
    assert saved is not None
    assert saved.effective_policy["kubernetes_workload_security"] == effective_security


def _proof(
    *,
    kind: str,
    image: str,
    identity: str,
    effective_policy: dict,
) -> WorkerValidationRecord:
    return WorkerValidationRecord(
        id=f"proof-{identity[-12:]}",
        kind=kind,
        image=image,
        image_digest=identity,
        contract_version=GOBLIN_CONTAINER_CONTRACT_VERSION,
        validator_version=VALIDATOR_VERSION,
        validated_at=utc_now(),
        status="passed",
        effective_policy=effective_policy,
    )
