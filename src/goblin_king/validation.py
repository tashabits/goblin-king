"""Contract validation helpers for container-backed goblin workers."""

from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from goblin_king.contracts import GoblinDefinition, GoblinResult, WorkerValidationRecord
from goblin_king.registry import GoblinRegistry
from goblin_king.runtime import DockerRuntime, new_run_context
from goblin_king.versions import GOBLIN_CONTAINER_CONTRACT_VERSION
from goblin_king.workers import WorkerConfigError, WorkerImageMap

VALIDATOR_VERSION = "goblin-king-validator/v1"


class WorkerValidationResult(BaseModel):
    """One worker contract validation outcome."""

    kind: str
    ok: bool
    image: str | None = None
    image_digest: str | None = None
    contract_version: str = GOBLIN_CONTAINER_CONTRACT_VERSION
    validator_version: str = VALIDATOR_VERSION
    validated_at: datetime | None = None
    result_status: str | None = None
    result_file: str | None = None
    artifact_count: int = 0
    error: str | None = None
    checks: list[str] = Field(default_factory=list)
    exit_code: int | None = None
    stdout: str | None = None
    stderr: str | None = None


def validation_record(
    result: WorkerValidationResult,
    *,
    effective_policy: dict[str, Any] | None = None,
) -> WorkerValidationRecord:
    """Convert a validation attempt into the durable scheduler gate record."""
    return WorkerValidationRecord(
        id=str(uuid4()),
        kind=result.kind,
        image=result.image or "",
        image_digest=result.image_digest or "",
        contract_version=result.contract_version,
        validator_version=result.validator_version,
        validated_at=result.validated_at or datetime.now().astimezone(),
        status="passed" if result.ok else "failed",
        failure_reasons=[] if result.ok else [result.error or "validation failed"],
        effective_policy=effective_policy or {},
    )


def validate_workers(
    *,
    registry: GoblinRegistry,
    workers: WorkerImageMap,
    input_payload: dict[str, Any],
    kinds: list[str] | None = None,
    build: bool = False,
    require_success: bool = False,
    prebuilt_image: bool = False,
    timeout_seconds: int | None = None,
    redis_url: str = "redis://localhost:6379/0",
    run_root: str | Path | None = None,
) -> list[WorkerValidationResult]:
    """Build/run selected workers and validate their result envelopes."""
    definitions = registry.list()
    if kinds:
        requested = set(kinds)
        definitions = [definition for definition in definitions if definition.kind in requested]
        missing = sorted(requested - {definition.kind for definition in definitions})
        results = [
            WorkerValidationResult(kind=kind, ok=False, error=f"unknown goblin kind: {kind}")
            for kind in missing
        ]
    else:
        results = []

    with TemporaryDirectory(prefix="goblin-contract-validation-") as temp_dir:
        root = Path(temp_dir)
        runtime = DockerRuntime(
            workers=workers,
            redis_url=redis_url,
            run_root=run_root or root / "runs",
        )
        for definition in definitions:
            results.append(
                _validate_one(
                    runtime=runtime,
                    workers=workers,
                    kind=definition.kind,
                    input_payload=input_payload,
                    build=build,
                    require_success=require_success,
                    prebuilt_image=prebuilt_image,
                    timeout_seconds=timeout_seconds,
                )
            )
    return results


def _validate_one(
    *,
    runtime: DockerRuntime,
    workers: WorkerImageMap,
    kind: str,
    input_payload: dict[str, Any],
    build: bool,
    require_success: bool,
    prebuilt_image: bool,
    timeout_seconds: int | None,
) -> WorkerValidationResult:
    checks: list[str] = []
    try:
        worker = workers.get(kind)
        image_digest: str | None = None
        if prebuilt_image:
            image_digest, image_error = inspect_image_identity(
                runtime.docker_executable, worker.image
            )
            if image_error is not None:
                return WorkerValidationResult(
                    kind=kind,
                    ok=False,
                    image=worker.image,
                    validated_at=datetime.now().astimezone(),
                    error=image_error,
                    checks=checks,
                )
            checks.append("image")
        else:
            context_path = workers.resolved_context(worker)
            dockerfile = context_path / worker.dockerfile
            if not context_path.is_dir():
                return WorkerValidationResult(
                    kind=kind,
                    ok=False,
                    image=worker.image,
                    error=f"worker context missing: {context_path}",
                )
            checks.append("context")
            if not dockerfile.is_file():
                return WorkerValidationResult(
                    kind=kind,
                    ok=False,
                    image=worker.image,
                    error=f"worker Dockerfile missing: {dockerfile}",
                    checks=checks,
                )
            checks.append("dockerfile")
            if build:
                runtime.build_image(kind)
                checks.append("build")
            image_digest, image_error = inspect_image_identity(
                runtime.docker_executable, worker.image
            )
            if image_error is not None:
                return WorkerValidationResult(
                    kind=kind,
                    ok=False,
                    image=worker.image,
                    validated_at=datetime.now().astimezone(),
                    error=image_error,
                    checks=checks,
                )
    except WorkerConfigError as error:
        return WorkerValidationResult(
            kind=kind,
            ok=False,
            validated_at=datetime.now().astimezone(),
            error=str(error),
            checks=checks,
        )

    context = new_run_context(f"validation-{kind}", kind)
    context = context.model_copy(
        update={"artifact_root": str(runtime.run_root.parent / "artifacts" / context.run_id)}
    )
    result = runtime.run(
        definition=GoblinDefinition(kind=kind, display_name=kind, module="container.only"),
        _entrypoint=None,
        input_payload=input_payload,
        context=context,
        timeout_seconds=timeout_seconds,
    )
    result_file = runtime.run_root / context.run_id / "result.json"
    if not result_file.is_file():
        return WorkerValidationResult(
            kind=kind,
            ok=False,
            image=worker.image,
            image_digest=image_digest,
            validated_at=datetime.now().astimezone(),
            result_status=result.status,
            error=result.error or "worker did not write result.json",
            checks=checks,
        )
    checks.append("result-file")

    try:
        parsed = GoblinResult.model_validate_json(result_file.read_text(encoding="utf-8"))
    except Exception as error:
        return WorkerValidationResult(
            kind=kind,
            ok=False,
            image=worker.image,
            image_digest=image_digest,
            validated_at=datetime.now().astimezone(),
            result_file=str(result_file),
            error=f"result envelope invalid: {error}",
            checks=checks,
        )
    checks.append("result-envelope")
    if parsed.metrics:
        checks.append("metrics")
    if parsed.handoff:
        checks.append("handoff")

    missing_artifacts = [
        artifact.name
        for artifact in parsed.artifacts
        if artifact.uri.startswith("artifact://")
        and not (Path(context.artifact_root) / artifact.name).exists()
    ]
    if missing_artifacts:
        return WorkerValidationResult(
            kind=kind,
            ok=False,
            image=worker.image,
            image_digest=image_digest,
            validated_at=datetime.now().astimezone(),
            result_status=parsed.status,
            result_file=str(result_file),
            artifact_count=len(parsed.artifacts),
            error=f"artifact metadata points to missing files: {', '.join(missing_artifacts)}",
            checks=checks,
        )
    checks.append("artifacts")

    if require_success and parsed.status != "success":
        return WorkerValidationResult(
            kind=kind,
            ok=False,
            image=worker.image,
            image_digest=image_digest,
            validated_at=datetime.now().astimezone(),
            result_status=parsed.status,
            result_file=str(result_file),
            artifact_count=len(parsed.artifacts),
            error=parsed.error or "worker returned failed status",
            checks=checks,
        )

    return WorkerValidationResult(
        kind=kind,
        ok=True,
        image=worker.image,
        image_digest=image_digest,
        validated_at=datetime.now().astimezone(),
        result_status=parsed.status,
        result_file=str(result_file),
        artifact_count=len(parsed.artifacts),
        checks=checks,
    )


def inspect_image_identity(docker_executable: str, image: str) -> tuple[str | None, str | None]:
    """Return the local immutable image identity or a clear availability error."""
    completed = subprocess.run(
        [docker_executable, "image", "inspect", image, "--format", "{{.Id}}"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode == 0:
        digest = completed.stdout.strip()
        return digest or None, None
    detail = completed.stderr.strip() or completed.stdout.strip()
    return None, f"worker image unavailable: {image}; {detail}"
