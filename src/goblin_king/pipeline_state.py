"""Transactional delivery state, dispatch outbox, and fenced release operations."""

from __future__ import annotations

import json
import re
from contextlib import contextmanager
from uuid import NAMESPACE_URL, uuid4, uuid5

from sqlalchemy import delete, select

from goblin_king.contracts import JobRecord, utc_now
from goblin_king.pipeline_contracts import PipelineCatalog, PipelineRegistration, SourceChange
from goblin_king.pipeline_faults import no_fault
from goblin_king.pipeline_store import PipelineAdmissionStore, _fingerprint
from goblin_king.store import SQLiteStore
from goblin_king.store_schema import (
    pipeline_actions_table as actions,
)
from goblin_king.store_schema import (
    pipeline_builds_table as builds,
)
from goblin_king.store_schema import (
    pipeline_dispatch_table as dispatch,
)
from goblin_king.store_schema import (
    pipeline_environment_states_table as environments,
)
from goblin_king.store_schema import (
    pipeline_journal_table as journal,
)
from goblin_king.store_schema import pipeline_notifications_table as notifications
from goblin_king.store_schema import (
    pipeline_operations_table as operations,
)
from goblin_king.store_schema import (
    pipeline_provider_cursors_table as cursors,
)
from goblin_king.store_schema import (
    pipeline_registrations_table as registrations,
)
from goblin_king.store_schema import (
    pipeline_reservations_table as reservations,
)


class PipelineConflict(ValueError):
    """A version, destination, idempotency key, or external ownership conflicts."""


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def registration_key(registration):
    return _fingerprint([registration.project_id, registration.service_id])


def release_key(registration, name):
    return _fingerprint([registration_key(registration), name])


class PipelineState:
    """Keep authorization changes, desired state, and work intent on one SQLite boundary."""

    def __init__(self, store: SQLiteStore):
        self.store = store
        self.fault_hook = no_fault

    def authorize_scheduler_job(self, job):
        """Reject direct submissions that did not originate in the durable dispatch outbox."""
        with self.store.engine.connect() as connection:
            row = self.row(connection, dispatch, job.id)
        if row is None:
            return "pipeline job has no authenticated dispatch intent"
        original = JobRecord.model_validate(row["document"])
        fields = {
            "id",
            "kind",
            "input",
            "metadata",
            "project_id",
            "created_by",
            "timeout_seconds",
            "max_retries",
        }
        if original.model_dump(include=fields) != job.model_dump(include=fields):
            return "pipeline submission differs from its authenticated dispatch intent"
        return None

    @contextmanager
    def transaction(self):
        with self.store.engine.connect() as connection:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            try:
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    @staticmethod
    def row(connection, table, key):
        column = table.c.id if "id" in table.c else table.c.key
        row = connection.execute(select(table).where(column == key)).mappings().one_or_none()
        if row is None:
            return None
        return dict(row) | {"document": json.loads(row["document"])}

    @staticmethod
    def audit(connection, project, entity, action, evidence):
        timestamp = utc_now().isoformat()
        inserted = connection.execute(
            journal.insert().values(
                project_id=project,
                entity=entity,
                action=action,
                at=timestamp,
                evidence=encoded(evidence),
            )
        )
        sequence = inserted.inserted_primary_key[0]
        event_id = str(
            uuid5(NAMESPACE_URL, encoded([project, entity, action, timestamp, sequence]))
        )
        connection.execute(
            notifications.insert().values(
                id=event_id,
                status="pending",
                attempts=0,
                not_before=0,
                document=encoded(
                    {
                        "id": event_id,
                        "project_id": project,
                        "entity": entity,
                        "action": action,
                        "at": timestamp,
                        "journal_sequence": sequence,
                    }
                ),
            )
        )

    @staticmethod
    def cas(connection, table, row, document, **fields):
        column = table.c.id if "id" in table.c else table.c.key
        key = row.get("id", row.get("key"))
        changed = connection.execute(
            table.update()
            .where(column == key, table.c.version == row["version"])
            .values(version=row["version"] + 1, document=encoded(document), **fields)
        )
        if changed.rowcount != 1:
            raise PipelineConflict("state version changed")

    @staticmethod
    def expected(row, version):
        if (row["version"] if row else 0) != version:
            raise PipelineConflict("expected version does not match current state")

    def action_receipt(self, key, fingerprint):
        with self.store.engine.connect() as connection:
            prior = (
                connection.execute(select(actions).where(actions.c.key == key))
                .mappings()
                .one_or_none()
            )
        if prior:
            if prior["fingerprint"] != fingerprint:
                raise PipelineConflict("idempotency key conflicts")
            return json.loads(prior["response"])
        return None

    @staticmethod
    def attachment_identity(registration, repository_id, expected_version, actor, idempotency_key):
        return (
            _fingerprint([registration.project_id, "attach", idempotency_key]),
            _fingerprint(
                [registration.model_dump(mode="json"), repository_id, expected_version, actor]
            ),
        )

    def attach(
        self,
        registration: PipelineRegistration,
        *,
        expected_version: int,
        repository_id: int,
        installation_id: int,
        discovery: dict,
        worker: dict,
        actor: str,
        idempotency_key: str,
    ):
        """Reserve globally unique routes without admitting any existing source head."""
        registration = PipelineRegistration.model_validate(registration.model_dump())
        if type(repository_id) is not int or repository_id <= 0 or installation_id <= 0:
            raise ValueError("numeric repository and installation identity required")
        if not re.fullmatch(r"[^\s@]+@sha256:[0-9a-f]{64}", worker.get("image", "")):
            raise ValueError("worker image must be pinned by digest")
        key = registration_key(registration)
        document = {
            "registration": registration.model_dump(mode="json"),
            "repository_id": repository_id,
            "installation_id": installation_id,
            "discovery": discovery,
            "worker": worker,
        }
        action_key, fingerprint = self.attachment_identity(
            registration, repository_id, expected_version, actor, idempotency_key
        )
        with self.transaction() as connection:
            prior = (
                connection.execute(select(actions).where(actions.c.key == action_key))
                .mappings()
                .one_or_none()
            )
            if prior:
                if prior["fingerprint"] != fingerprint:
                    raise PipelineConflict("idempotency key reused with different parameters")
                return json.loads(prior["response"])
            row = self.row(connection, registrations, key)
            self.expected(row, expected_version)
            catalog = [
                PipelineRegistration.model_validate(json.loads(r[0])["registration"])
                for r in connection.execute(
                    select(registrations.c.document).where(registrations.c.key != key)
                )
            ]
            try:
                PipelineCatalog(registrations=tuple([*catalog, registration]))
            except ValueError as error:
                raise PipelineConflict("repository or destination is already reserved") from error
            if row and row["enabled"]:
                raise PipelineConflict("disable management before changing registration")
            if row:
                old = PipelineRegistration.model_validate(row["document"]["registration"])
                if old.environments != registration.environments:
                    raise PipelineConflict("route changes require a separate migration")
                self.cas(connection, registrations, row, document)
            else:
                connection.execute(
                    registrations.insert().values(
                        key=key,
                        project_id=registration.project_id,
                        version=1,
                        enabled=0,
                        document=encoded(document),
                    )
                )
            connection.execute(delete(reservations).where(reservations.c.registration_key == key))
            destinations = [["repository", registration.provider, registration.source_repository]]
            for environment in registration.environments:
                destinations += [
                    ["release", environment.target, environment.namespace, environment.release],
                    ["registry", environment.image_repository],
                ]
                env_key = release_key(registration, environment.name)
                if self.row(connection, environments, env_key) is None:
                    connection.execute(
                        environments.insert().values(
                            key=env_key,
                            registration_key=key,
                            project_id=registration.project_id,
                            version=1,
                            document=encoded(
                                {
                                    "environment": environment.model_dump(mode="json"),
                                    "generation": 0,
                                    "desired_commit": None,
                                    "desired_build": None,
                                    "active": environment.active,
                                    "observed": None,
                                    "hold": None,
                                    "operation_id": None,
                                }
                            ),
                        )
                    )
            for destination in destinations:
                connection.execute(
                    reservations.insert().values(
                        destination=_fingerprint(destination), registration_key=key
                    )
                )
            response = {"key": key, "version": expected_version + 1, "enabled": False}
            connection.execute(
                actions.insert().values(
                    key=action_key, fingerprint=fingerprint, response=encoded(response)
                )
            )
            self.audit(
                connection,
                registration.project_id,
                key,
                "repository.attached",
                {"actor": actor, "version": response["version"]},
            )
            return response

    def management(self, key, *, expected_version, enabled, heads, actor, idempotency_key=None):
        """Set management and seed authoritative branch cursors without historical dispatch."""
        with self.transaction() as connection:
            row = self.row(connection, registrations, key)
            if row is None:
                raise KeyError(key)
            action_key = _fingerprint([row["project_id"], key, "management", idempotency_key])
            fingerprint = _fingerprint([expected_version, enabled, actor])
            if idempotency_key:
                prior = (
                    connection.execute(select(actions).where(actions.c.key == action_key))
                    .mappings()
                    .one_or_none()
                )
                if prior:
                    if prior["fingerprint"] != fingerprint:
                        raise PipelineConflict("idempotency key conflicts")
                    return json.loads(prior["response"])
            self.expected(row, expected_version)
            registration = PipelineRegistration.model_validate(row["document"]["registration"])
            if enabled:
                if set(heads) != {e.branch for e in registration.environments}:
                    raise ValueError("authoritative heads are required for all configured branches")
                for environment in registration.environments:
                    cursor_key = release_key(registration, environment.name)
                    cursor = self.row(connection, cursors, cursor_key)
                    document = {"commit": heads[environment.branch], "seeded": True}
                    if cursor:
                        self.cas(connection, cursors, cursor, document)
                    else:
                        connection.execute(
                            cursors.insert().values(
                                key=cursor_key, version=1, document=encoded(document)
                            )
                        )
            self.cas(connection, registrations, row, row["document"], enabled=int(enabled))
            self.audit(
                connection,
                row["project_id"],
                key,
                "repository.management",
                {"actor": actor, "enabled": enabled},
            )
            if idempotency_key:
                connection.execute(
                    actions.insert().values(
                        key=action_key,
                        fingerprint=fingerprint,
                        response=encoded(
                            {"key": key, "version": expected_version + 1, "enabled": enabled}
                        ),
                    )
                )
        return {"key": key, "version": expected_version + 1, "enabled": enabled}

    def admit(
        self, key, change: SourceChange, *, expected_version, expected_cursor, verification: dict
    ):
        """Atomically admit provider-verified current source, desired generation, and dispatch."""
        with self.store.engine.connect() as connection:
            row = self.row(connection, registrations, key)
        if row is None:
            raise KeyError(key)
        registration = PipelineRegistration.model_validate(row["document"]["registration"])
        environment = registration.environment_for(change)
        env_key = release_key(registration, environment.name)

        def persist(connection, admission):
            current = self.row(connection, registrations, key)
            self.expected(current, expected_version)
            if not current["enabled"]:
                raise PipelineConflict("repository management is disabled")
            if current["document"] != row["document"]:
                raise PipelineConflict("registration changed during verification")
            cursor = self.row(connection, cursors, env_key)
            if not cursor or cursor["document"]["commit"] not in {expected_cursor, change.commit}:
                raise PipelineConflict("provider cursor changed; verify ancestry again")
            if not admission.created:
                return  # Historical admissions are never upgraded into executable work.
            if cursor["document"]["commit"] == change.commit:
                raise PipelineConflict("source head was already observed or seeded at enablement")
            build_id = admission.request.build_id
            state = self.row(connection, environments, env_key)
            desired = state["document"] | {
                "generation": state["document"]["generation"] + 1,
                "desired_commit": change.commit,
                "desired_build": build_id,
            }
            self.cas(connection, environments, state, desired)
            self.cas(connection, cursors, cursor, {"commit": change.commit, "seeded": False})
            document = {
                "request": admission.request.model_dump(mode="json"),
                "release_key": env_key,
                "generation": desired["generation"],
                "verification": verification,
                "result": None,
            }
            connection.execute(
                builds.insert().values(
                    id=build_id,
                    project_id=registration.project_id,
                    version=1,
                    status="admitted",
                    document=encoded(document),
                )
            )
            dispatch_id = str(uuid5(NAMESPACE_URL, f"goblin-king/pipeline/{build_id}/build/1"))
            job = JobRecord(
                id=dispatch_id,
                kind="pipeline.build",
                project_id=registration.project_id,
                input={
                    "request": admission.request.model_dump(mode="json"),
                    "repository_id": row["document"]["repository_id"],
                    "contract": verification.get("contract", {}),
                },
                created_at=utc_now(),
                created_by="pipeline",
                timeout_seconds=2700,
                max_retries=0,
                metadata={
                    "pipeline": {"build_id": build_id, **row["document"]["worker"]},
                    "resource_policy": row["document"]["worker"].get("resource_policy", {}),
                },
            )
            connection.execute(
                dispatch.insert().values(
                    id=dispatch_id,
                    build_id=build_id,
                    job_id=dispatch_id,
                    version=1,
                    status="pending",
                    document=job.model_dump_json(),
                )
            )
            self.audit(
                connection,
                registration.project_id,
                build_id,
                "source.admitted",
                {
                    "verification": verification,
                    "generation": desired["generation"],
                    "dispatch_id": dispatch_id,
                },
            )

        return PipelineAdmissionStore(self.store).admit(
            registration, change, transaction_hook=persist
        )

    def dispatch_pending(self, validate):
        """Replay stable submissions after an enqueue/ack crash; retain validation gates."""
        with self.store.engine.connect() as connection:
            pending = (
                connection.execute(select(dispatch.c.id).where(dispatch.c.status == "pending"))
                .scalars()
                .all()
            )
        for key in pending:
            with self.store.engine.connect() as connection:
                row = self.row(connection, dispatch, key)
            job = JobRecord.model_validate(row["document"])
            submitted = self.store.get_job(job.id) is not None
            if row["build_id"]:
                with self.transaction() as connection:
                    build = self.row(connection, builds, row["build_id"])
                    release = self.row(connection, environments, build["document"]["release_key"])
                    registration = self.row(connection, registrations, release["registration_key"])
                    if release["document"]["desired_build"] != row["build_id"] and not submitted:
                        self.cas(connection, dispatch, row, row["document"], status="superseded")
                        self.cas(
                            connection,
                            builds,
                            build,
                            build["document"]
                            | {"dispatch_error": "a newer desired build superseded this attempt"},
                            status="superseded",
                        )
                        continue
                    if not registration["enabled"] and not submitted:
                        continue
            try:
                validate(job)
            except PipelineConflict as error:
                with self.transaction() as connection:
                    table = builds if row["build_id"] else operations
                    entity_id = row["build_id"] or job.metadata["pipeline"]["operation_id"]
                    current = self.row(connection, table, entity_id)
                    if current["document"].get("dispatch_error") != str(error):
                        self.cas(
                            connection,
                            table,
                            current,
                            current["document"] | {"dispatch_error": str(error)},
                        )
                        self.audit(
                            connection,
                            job.project_id,
                            entity_id,
                            "dispatch.blocked",
                            {"reason": str(error)},
                        )
                continue
            self.fault_hook("dispatch.before_submit", job.id)
            self.store.submit_job_once(job)
            self.fault_hook("dispatch.after_submit", job.id)
            with self.transaction() as connection:
                current = self.row(connection, dispatch, key)
                if current["status"] == "pending":
                    self.cas(
                        connection, dispatch, current, current["document"], status="dispatched"
                    )
                    self.audit(
                        connection, job.project_id, key, "dispatch.acknowledged", {"job_id": job.id}
                    )
            self.fault_hook("dispatch.after_ack", job.id)

    def finish_build(self, build_id, result, *, job_id, run_id):
        """Persist inspected results without changing a previously observed healthy release."""
        with self.transaction() as connection:
            row = self.row(connection, builds, build_id)
            if row is None:
                raise KeyError(build_id)
            if row["document"]["result"] is not None:
                if row["document"]["result"] != result:
                    raise PipelineConflict("build already has a different result")
                return
            request = row["document"]["request"]
            data = result.get("data", {})
            if result["status"] == "success":
                if (
                    data.get("build_id") != build_id
                    or data.get("source_revision") != request["source"]["commit"]
                    or data.get("pipeline_revision") != request["registration"]["pipeline_revision"]
                    or not re.fullmatch(r"sha256:[0-9a-f]{64}", data.get("image_digest", ""))
                ):
                    raise PipelineConflict("build provenance does not match admission")
                release = self.row(connection, environments, row["document"]["release_key"])
                status = (
                    "published"
                    if release["document"]["desired_build"] == build_id
                    else "superseded"
                )
            else:
                status = "held" if data.get("recovery_hold") else "failed"
            document = row["document"] | {"result": result, "job_id": job_id, "run_id": run_id}
            self.cas(connection, builds, row, document, status=status)
            self.audit(
                connection,
                row["project_id"],
                build_id,
                "build." + status,
                {"job_id": job_id, "run_id": run_id},
            )

    def recover_build(
        self, build_id, *, expected_version, actor, idempotency_key, termination=None
    ):
        """Settle a fenced interruption without publishing or accepting its artifact."""
        with self.transaction() as connection:
            original = self.row(connection, builds, build_id)
            if original is None:
                raise KeyError(build_id)
            key = _fingerprint([original["project_id"], build_id, "recover", idempotency_key])
            fingerprint = _fingerprint([expected_version, actor])
            prior = (
                connection.execute(select(actions).where(actions.c.key == key))
                .mappings()
                .one_or_none()
            )
            if prior:
                if prior["fingerprint"] != fingerprint:
                    raise PipelineConflict("idempotency key conflicts")
                return json.loads(prior["response"])
            self.expected(original, expected_version)
            if original["status"] != "held":
                raise PipelineConflict("only held builds need execution recovery")
            if termination is None:
                return None  # Receipt lookup before contacting external execution evidence.
            if (
                not termination.get("terminated")
                or termination.get("build_id") != build_id
                or termination.get("job_id") != original["document"].get("job_id")
                or not termination.get("source_namespace")
            ):
                raise PipelineConflict("prior build execution termination is not established")
            document = original["document"] | {"termination": termination, "recovery_actor": actor}
            self.cas(connection, builds, original, document, status="failed")
            operation_id = str(uuid5(NAMESPACE_URL, "goblin-king/build-recovery/" + key))
            response = {
                "operation_id": operation_id,
                "build_id": build_id,
                "version": expected_version + 1,
                "status": "failed",
            }
            connection.execute(
                actions.insert().values(
                    key=key, fingerprint=fingerprint, response=encoded(response)
                )
            )
            self.audit(
                connection,
                original["project_id"],
                build_id,
                "build.recovered",
                {"operation_id": operation_id, "actor": actor, "termination": termination},
            )
            return response

    def rebuild(self, build_id, *, expected_version, actor, idempotency_key):
        """Allocate a distinct explicit attempt without replaying an automatic admission."""
        with self.transaction() as connection:
            original = self.row(connection, builds, build_id)
            if original is None:
                raise KeyError(build_id)
            action_key = _fingerprint(
                [original["project_id"], build_id, "rebuild", idempotency_key]
            )
            fingerprint = _fingerprint([expected_version, actor])
            prior = (
                connection.execute(select(actions).where(actions.c.key == action_key))
                .mappings()
                .one_or_none()
            )
            if prior:
                if prior["fingerprint"] != fingerprint:
                    raise PipelineConflict("idempotency key conflicts")
                return json.loads(prior["response"])
            self.expected(original, expected_version)
            if original["status"] not in {"failed", "published", "superseded"}:
                raise PipelineConflict(
                    "only settled builds can be rebuilt; inspect interrupted execution first"
                )
            release = self.row(connection, environments, original["document"]["release_key"])
            registration = self.row(connection, registrations, release["registration_key"])
            if not registration["enabled"]:
                raise PipelineConflict("management is disabled")
            source = original["document"]["request"]["source"]
            if source["commit"] != release["document"]["desired_commit"]:
                raise PipelineConflict("a newer revision is desired; rebuild that revision instead")
            new_id = str(uuid4())
            request = original["document"]["request"] | {
                "build_id": new_id,
                "created_at": utc_now().isoformat(),
                "registration": registration["document"]["registration"],
            }
            generation = release["document"]["generation"] + 1
            state = release["document"] | {"desired_build": new_id, "generation": generation}
            self.cas(connection, environments, release, state)
            document = {
                "request": request,
                "generation": generation,
                "release_key": release["key"],
                "result": None,
                "verification": original["document"]["verification"],
                "rebuild_of": build_id,
            }
            connection.execute(
                builds.insert().values(
                    id=new_id,
                    project_id=original["project_id"],
                    version=1,
                    status="admitted",
                    document=encoded(document),
                )
            )
            job_id = str(uuid5(NAMESPACE_URL, f"goblin-king/pipeline/{new_id}/build/1"))
            binding = registration["document"]["worker"]
            job = JobRecord(
                id=job_id,
                kind="pipeline.build",
                project_id=original["project_id"],
                created_at=utc_now(),
                created_by="pipeline",
                timeout_seconds=2700,
                max_retries=0,
                input={
                    "request": request,
                    "repository_id": registration["document"]["repository_id"],
                    "contract": original["document"]["verification"]["contract"],
                },
                metadata={
                    "pipeline": {"build_id": new_id, **binding},
                    "resource_policy": binding["resource_policy"],
                },
            )
            connection.execute(
                dispatch.insert().values(
                    id=job_id,
                    job_id=job_id,
                    build_id=new_id,
                    version=1,
                    status="pending",
                    document=job.model_dump_json(),
                )
            )
            response = {"operation_id": job_id, "build_id": new_id}
            connection.execute(
                actions.insert().values(
                    key=action_key, fingerprint=fingerprint, response=encoded(response)
                )
            )
            self.audit(
                connection,
                original["project_id"],
                new_id,
                "build.rebuilt",
                {"actor": actor, "previous_build": build_id, "job_id": job_id},
            )
            return response

    def begin_operation(
        self,
        key,
        *,
        expected_version,
        action,
        actor,
        idempotency_key,
        payload=None,
        automatic=False,
    ):
        """Acquire exclusive release ownership; lease expiry never steals this lock."""
        if action not in {"deploy", "activate", "deactivate", "rollback", "record", "clear-hold"}:
            raise ValueError("unsupported release action")
        payload = payload or {}
        with self.transaction() as connection:
            row = self.row(connection, environments, key)
            if row is None:
                raise KeyError(key)
            action_key = _fingerprint([row["project_id"], key, idempotency_key])
            fingerprint = _fingerprint([expected_version, action, actor, payload])
            prior = (
                connection.execute(select(actions).where(actions.c.key == action_key))
                .mappings()
                .one_or_none()
            )
            if prior:
                # Automatic admission is bound to one build, independent of later versions.
                if (
                    automatic
                    and actor == "pipeline"
                    and action == "deploy"
                    and idempotency_key == ("automatic-" + str(row["document"]["desired_build"]))
                ):
                    return json.loads(prior["response"])
                if prior["fingerprint"] != fingerprint:
                    raise PipelineConflict("idempotency key conflicts")
                return json.loads(prior["response"])
            self.expected(row, expected_version)
            state = row["document"]
            if state["operation_id"]:
                raise PipelineConflict(
                    "release operation is still owned; recovery must confirm termination"
                )
            if state["hold"] and action not in {"clear-hold", "rollback", "record", "deactivate"}:
                raise PipelineConflict("release is held")
            if action == "deactivate" and state["environment"]["name"] != "test":
                raise ValueError("deactivation is supported only for test")
            operation_id = str(uuid4())
            document = {
                "action": action,
                "actor": actor,
                "payload": payload,
                "generation": state["generation"],
                "before": state["observed"],
                "worker": None,
                "evidence": [],
                "started_at": utc_now().isoformat(),
            }
            connection.execute(
                operations.insert().values(
                    id=operation_id,
                    project_id=row["project_id"],
                    release_key=key,
                    version=1,
                    status="pending",
                    document=encoded(document),
                )
            )
            self.cas(connection, environments, row, state | {"operation_id": operation_id})
            response = {"operation_id": operation_id, "version": expected_version + 1}
            connection.execute(
                actions.insert().values(
                    key=action_key, fingerprint=fingerprint, response=encoded(response)
                )
            )
            self.audit(connection, row["project_id"], operation_id, "operation.started", document)
            return response

    def list(self, table, *, project_id):
        """Read project-scoped state; callers must authorize the project first."""
        if table not in (registrations, environments, builds, operations):
            raise ValueError("unsupported public collection")
        with self.store.engine.connect() as connection:
            return [
                dict(row) | {"document": json.loads(row["document"])}
                for row in connection.execute(
                    select(table).where(table.c.project_id == project_id)
                ).mappings()
            ]
