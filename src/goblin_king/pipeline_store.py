"""Atomic pipeline admission on the existing local SQLite persistence boundary."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.engine import Connection

from goblin_king.contracts import utc_now
from goblin_king.pipeline_contracts import (
    PipelineBuildRequest,
    PipelineRegistration,
    SourceChange,
)
from goblin_king.pipeline_hashing import fingerprint as _fingerprint
from goblin_king.store import SQLiteStore
from goblin_king.store_schema import pipeline_build_requests_table, pipeline_receipts_table


class PipelineEventConflict(ValueError):
    """A delivery ID was reused with different source data or different routing."""


@dataclass(frozen=True)
class PipelineAdmission:
    """Return the original request and whether this transaction created it."""

    request: PipelineBuildRequest
    created: bool


def _request_from_row(row: Any) -> PipelineBuildRequest:
    """Restore the immutable snapshot with its database-assigned pagination sequence."""
    snapshot = json.loads(row["snapshot_json"])
    return PipelineBuildRequest.model_validate(snapshot | {"sequence": row["sequence"]})


class PipelineAdmissionStore:
    """Persist source receipts and build intent, without submitting scheduler work.

    This adapter uses the current SQLiteStore and its local transaction semantics.
    It is not a distributed release lock, webhook authenticator, branch protection
    verifier, or a claim that SQLite supports a multi-node controller deployment.
    """

    def __init__(self, store: SQLiteStore) -> None:
        """Reuse the application's initialized store and additive schema migrations."""
        self.store = store

    def admit(
        self,
        registration: PipelineRegistration,
        change: SourceChange,
        *,
        transaction_hook: Callable[[Connection, PipelineAdmission], None] | None = None,
    ) -> PipelineAdmission:
        """Atomically record approved source intent and deduplicate retried notifications.

        The caller must authenticate the event and authorize the operator catalog.
        One automatic request is admitted per project/service/repository/environment/
        source commit. A config change or notification redelivery cannot silently
        rebuild that revision; explicit rebuilds need a separate future operation.
        """
        # Revalidate even model_construct inputs at this persistence boundary.
        registration = PipelineRegistration.model_validate(registration.model_dump())
        change = SourceChange.model_validate(change.model_dump())
        environment = registration.environment_for(change)
        identity = [
            registration.project_id,
            registration.service_id,
            change.provider,
            change.repository,
        ]
        request_key = _fingerprint([*identity, environment.name, change.commit])
        receipt_key = _fingerprint([*identity, change.delivery_id])
        payload_hash = _fingerprint(change.model_dump(mode="json"))
        with self.store.engine.connect() as connection:
            # Serialize the read/insert pair across independent controller connections.
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            try:
                receipt = (
                    connection.execute(
                        select(pipeline_receipts_table).where(
                            pipeline_receipts_table.c.receipt_key == receipt_key
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if receipt is not None and (
                    receipt["payload_hash"] != payload_hash or receipt["request_key"] != request_key
                ):
                    raise PipelineEventConflict(
                        "delivery ID conflicts with a recorded source event"
                    )
                row = (
                    connection.execute(
                        select(pipeline_build_requests_table).where(
                            pipeline_build_requests_table.c.request_key == request_key
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                created = row is None
                if row is None:
                    if receipt is not None:
                        raise RuntimeError("source receipt refers to a missing build request")
                    request = PipelineBuildRequest(
                        sequence=1,  # Replaced by the database sequence after insertion.
                        build_id=str(uuid4()),
                        created_at=utc_now(),
                        registration=registration,
                        source=change,
                        environment_name=environment.name,
                    )
                    inserted = connection.execute(
                        pipeline_build_requests_table.insert().values(
                            build_id=request.build_id,
                            request_key=request_key,
                            project_id=registration.project_id,
                            snapshot_json=request.model_dump_json(exclude={"sequence"}),
                        )
                    )
                    request = request.model_copy(
                        update={"sequence": inserted.inserted_primary_key[0]}
                    )
                else:
                    request = _request_from_row(row)
                if receipt is not None and receipt["build_id"] != request.build_id:
                    raise RuntimeError("source receipt identity does not match its build request")
                if receipt is None:
                    self._save_receipt(
                        connection, receipt_key, payload_hash, request_key, request.build_id
                    )
                admission = PipelineAdmission(request=request, created=created)
                if transaction_hook is not None:
                    transaction_hook(connection, admission)
                connection.commit()
                return admission
            except Exception:
                connection.rollback()
                raise

    def _save_receipt(
        self,
        connection: Connection,
        receipt_key: str,
        payload_hash: str,
        request_key: str,
        build_id: str,
    ) -> None:
        """Record delivery identity in the same transaction as its durable build intent."""
        connection.execute(
            pipeline_receipts_table.insert().values(
                receipt_key=receipt_key,
                payload_hash=payload_hash,
                request_key=request_key,
                build_id=build_id,
            )
        )

    def get(self, build_id: str, *, project_id: str) -> PipelineBuildRequest | None:
        """Read one admitted request within an explicit project scope."""
        with self.store.engine.connect() as connection:
            row = (
                connection.execute(
                    select(pipeline_build_requests_table).where(
                        pipeline_build_requests_table.c.build_id == build_id,
                        pipeline_build_requests_table.c.project_id == project_id,
                    )
                )
                .mappings()
                .one_or_none()
            )
        return _request_from_row(row) if row is not None else None

    def list_requests(
        self, *, project_id: str, after_sequence: int = 0, limit: int = 100
    ) -> list[PipelineBuildRequest]:
        """Page durable intent by sequence, without claiming or executing requests."""
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("limit must be an integer between 1 and 1000")
        if type(after_sequence) is not int or after_sequence < 0:
            raise ValueError("after_sequence must be a nonnegative integer")
        with self.store.engine.connect() as connection:
            rows = (
                connection.execute(
                    select(pipeline_build_requests_table)
                    .where(pipeline_build_requests_table.c.project_id == project_id)
                    .where(pipeline_build_requests_table.c.sequence > after_sequence)
                    .order_by(pipeline_build_requests_table.c.sequence)
                    .limit(limit)
                )
                .mappings()
                .all()
            )
        return [_request_from_row(row) for row in rows]
