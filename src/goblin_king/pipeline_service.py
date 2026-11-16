"""Shared delivery management and provider reconciliation for HTTP and CLI clients."""

from __future__ import annotations

import hashlib
import json
import threading

from sqlalchemy import select

from goblin_king.pipeline_contracts import PipelineRegistration, SourceChange
from goblin_king.pipeline_github import AppCredentials, GitHubProvider, UnapprovedSource
from goblin_king.pipeline_state import PipelineConflict, PipelineState, encoded, release_key
from goblin_king.store_schema import (
    pipeline_builds_table as builds,
)
from goblin_king.store_schema import (
    pipeline_dispatch_table as dispatch,
)
from goblin_king.store_schema import (
    pipeline_provider_cursors_table as cursors,
)
from goblin_king.store_schema import (
    pipeline_registrations_table as registrations,
)
from goblin_king.store_schema import (
    pipeline_webhooks_table as webhooks,
)
from goblin_king.store_schema import (
    runs_table,
)


class PipelineService:
    """Verify provider facts before atomically advancing managed desired state."""

    def __init__(self, store, settings, *, provider=None):
        self.settings = settings
        self.state = PipelineState(store)
        self.provider = provider or GitHubProvider(
            AppCredentials(
                settings.app_id,
                settings.installation_id,
                settings.private_key_path,
                settings.webhook_secret_path,
                settings.provider_url,
                settings.simulated_provider,
            )
        )
        self.stop = threading.Event()
        self.thread = None
        self.last_error = None
        self.dispatch_validator = None
        self.result_verifier = None
        self.operation_binding = None
        self.operation_verifier = None
        self.configuration_verifier = None
        self.execution_acknowledger = None

    def start(self):
        if not self.thread:
            self.thread = threading.Thread(target=self.loop, name="pipeline-reconcile", daemon=True)
            self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=5)

    def loop(self):
        while not self.stop.is_set():
            try:
                from goblin_king.pipeline_notifications import deliver_notifications

                deliver_notifications(self.state, self.settings)
                self.reconcile()
                self.last_error = None
            except Exception as error:
                self.last_error = str(error)
            self.stop.wait(self.settings.reconcile_seconds)

    def discover(self, registration, repository_id):
        if registration.pipeline_revision != self.settings.pipeline_revision:
            raise ValueError("registration must use the installed pipeline revision")
        for route in registration.environments:
            if not any(
                route.namespace.startswith(prefix) for prefix in self.settings.namespace_prefixes
            ):
                raise ValueError("namespace is outside approved deployment bounds")
            if not any(
                route.image_repository.startswith(prefix.rstrip("/") + "/")
                for prefix in self.settings.registry_prefixes
            ):
                raise ValueError("registry destination is outside approved publication bounds")
            if route.deployment_profile not in self.settings.deployment_profiles:
                raise ValueError("deployment profile is not installed")
        if self.configuration_verifier:
            self.configuration_verifier(registration)
        return self.provider.discover(registration, repository_id)

    def attach(self, registration, repository_id, expected_version, actor, idempotency_key):
        replay = self.state.action_receipt(
            *self.state.attachment_identity(
                registration, repository_id, expected_version, actor, idempotency_key
            )
        )
        if replay:
            return replay
        discovery = self.discover(registration, repository_id)
        return self.state.attach(
            registration,
            expected_version=expected_version,
            repository_id=repository_id,
            installation_id=self.settings.installation_id,
            discovery=discovery,
            worker={
                "image": self.settings.worker_image,
                "profile": self.settings.worker_profile,
                "resource_policy": self.settings.worker_policy.compact(),
            },
            actor=actor,
            idempotency_key=idempotency_key,
        )

    def get_registration(self, key):
        with self.state.store.engine.connect() as connection:
            row = self.state.row(connection, registrations, key)
        if row is None:
            raise KeyError(key)
        return row

    def management(self, key, expected_version, enabled, actor, idempotency_key=None):
        row = self.get_registration(key)
        if idempotency_key:
            from goblin_king.pipeline_store import _fingerprint

            replay = self.state.action_receipt(
                _fingerprint([row["project_id"], key, "management", idempotency_key]),
                _fingerprint([expected_version, enabled, actor]),
            )
            if replay:
                return replay
        registration = PipelineRegistration.model_validate(row["document"]["registration"])
        heads = {}
        if enabled:
            discovery = self.discover(registration, row["document"]["repository_id"])
            heads = {route["branch"]: route["head"] for route in discovery["routes"]}
        return self.state.management(
            key,
            expected_version=expected_version,
            enabled=enabled,
            heads=heads,
            actor=actor,
            idempotency_key=idempotency_key,
        )

    def receive(self, raw, signature, delivery_id, event_type):
        if not delivery_id or len(delivery_id) > 200:
            raise UnapprovedSource("valid delivery ID required")
        event = self.provider.verify_webhook(raw, signature)
        if event_type == "ping":
            return {"status": "verified-ping"}
        if event_type != "push":
            return {"status": "ignored-event"}
        identity = f"{self.settings.installation_id}:{delivery_id}"
        fingerprint = hashlib.sha256(raw).hexdigest()
        with self.state.transaction() as connection:
            prior = (
                connection.execute(select(webhooks).where(webhooks.c.id == identity))
                .mappings()
                .one_or_none()
            )
            if prior:
                if prior["fingerprint"] != fingerprint:
                    raise PipelineConflict("delivery ID conflicts with prior authenticated bytes")
                return {"operation_id": identity, "status": prior["status"]}
            # Admission is asynchronous: only authenticated receipt exists at this point.
            connection.execute(
                webhooks.insert().values(
                    id=identity,
                    fingerprint=fingerprint,
                    status="received",
                    document=encoded({"event": event, "delivery_id": delivery_id}),
                )
            )
        return {"operation_id": identity, "status": "received"}

    def process(self, row, branch, commit, delivery_id, actor):
        if self.settings.recovery_mode:
            raise PipelineConflict("delivery is held in storage recovery mode")
        if not row["enabled"]:
            raise PipelineConflict("management is disabled")
        registration = PipelineRegistration.model_validate(row["document"]["registration"])
        environment = next((e for e in registration.environments if e.branch == branch), None)
        if environment is None:
            raise UnapprovedSource("unmanaged branch")
        with self.state.store.engine.connect() as connection:
            cursor = self.state.row(
                connection, cursors, release_key(registration, environment.name)
            )
        previous = cursor["document"]["commit"]
        if previous == commit:
            return {"status": "already-observed"}
        evidence = self.provider.verify_merge(
            registration, row["document"]["repository_id"], branch, commit, previous
        )
        if evidence is None:
            return {"status": "already-observed"}
        # Validate the contract at the exact commit, even when onboarding an older head succeeded.
        from goblin_king.pipeline_contracts import PipelineServiceContract

        contract = PipelineServiceContract.model_validate_json(
            self.provider.content(
                registration.source_repository,
                row["document"]["repository_id"],
                "pipeline-service.json",
                commit,
            )
        )
        evidence["contract"] = contract.model_dump(mode="json")
        admission = self.state.admit(
            row["key"],
            SourceChange(
                delivery_id=delivery_id,
                repository=registration.source_repository,
                branch=branch,
                commit=commit,
                actor=actor,
            ),
            expected_version=row["version"],
            expected_cursor=previous,
            verification=evidence,
        )
        return {"status": "admitted", "build_id": admission.request.build_id}

    def reconcile(self):
        with self.state.store.engine.connect() as connection:
            receipts = (
                connection.execute(select(webhooks).where(webhooks.c.status == "received"))
                .mappings()
                .all()
            )
            rows = [
                dict(row) | {"document": json.loads(row["document"])}
                for row in connection.execute(select(registrations)).mappings()
            ]
        errors = []
        for receipt in receipts:
            body = json.loads(receipt["document"])
            event = body["event"]
            try:
                row = next(
                    (
                        row
                        for row in rows
                        if row["document"]["repository_id"] == event.get("repository", {}).get("id")
                        and row["document"]["registration"]["source_repository"]
                        == event.get("repository", {}).get("full_name", "").lower()
                    ),
                    None,
                )
                if row is None:
                    raise UnapprovedSource("unregistered repository")
                ref = event.get("ref", "")
                if not ref.startswith("refs/heads/"):
                    raise UnapprovedSource("not a branch update")
                result = self.process(
                    row,
                    ref.removeprefix("refs/heads/"),
                    event.get("after", ""),
                    body["delivery_id"],
                    event.get("sender", {}).get("login", "github"),
                )
            except (UnapprovedSource, PipelineConflict, ValueError) as error:
                result = {"status": "rejected", "reason": str(error)}
            except Exception as error:
                errors.append(str(error))
                continue  # Keep receipt pending for bounded provider retries on the next poll.
            with self.state.transaction() as connection:
                connection.execute(
                    webhooks.update()
                    .where(webhooks.c.id == receipt["id"])
                    .values(status=result["status"], document=encoded(body | {"result": result}))
                )
        for row in rows:
            if not row["enabled"]:
                continue
            registration = PipelineRegistration.model_validate(row["document"]["registration"])
            for environment in registration.environments:
                try:
                    commit = self.provider.head(
                        registration.source_repository,
                        row["document"]["repository_id"],
                        environment.branch,
                    )
                    self.process(
                        row,
                        environment.branch,
                        commit,
                        f"poll-{row['key']}-{environment.name}-{commit}",
                        "github-poll",
                    )
                except (ValueError, PipelineConflict) as error:
                    errors.append(str(error))
                except Exception as error:
                    errors.append(str(error))
        if self.dispatch_validator and not self.settings.recovery_mode:
            self.state.dispatch_pending(self.dispatch_validator)
        if self.result_verifier:
            self.observe_builds()
        if self.operation_binding and not self.settings.recovery_mode:
            self.reconcile_operations()
        if self.settings.release_repository and not self.settings.recovery_mode:
            self.record_releases()
        if self.execution_acknowledger:
            self.acknowledge_executions()
        if errors:
            raise RuntimeError("; ".join(sorted(set(errors))))

    def acknowledge_executions(self):
        from goblin_king.store_schema import pipeline_operations_table as operations

        for table in (builds, operations):
            with self.state.store.engine.connect() as connection:
                rows = [
                    dict(row) | {"document": json.loads(row["document"])}
                    for row in connection.execute(
                        select(table).where(
                            table.c.status.in_(["published", "superseded", "completed", "failed"])
                        )
                    ).mappings()
                ]
            for row in rows:
                doc = row["document"]
                if (
                    not doc.get("job_id")
                    or not doc.get("result")
                    or doc.get("execution_acknowledged")
                ):
                    continue
                try:
                    self.execution_acknowledger(doc["job_id"], doc["result"])
                except Exception as error:
                    reason = (
                        "retained execution acknowledgement unavailable: " + type(error).__name__
                    )
                    with self.state.transaction() as connection:
                        current = self.state.row(connection, table, row["id"])
                        if current["document"].get("acknowledgement_error") != reason:
                            self.state.cas(
                                connection,
                                table,
                                current,
                                current["document"] | {"acknowledgement_error": reason},
                            )
                    continue
                with self.state.transaction() as connection:
                    current = self.state.row(connection, table, row["id"])
                    if (
                        current["document"].get("job_id") != doc["job_id"]
                        or current["document"].get("result") != doc["result"]
                    ):
                        raise PipelineConflict("execution changed before acknowledgement")
                    self.state.cas(
                        connection,
                        table,
                        current,
                        current["document"]
                        | {"execution_acknowledged": True, "acknowledgement_error": None},
                    )

    def observe_builds(self):
        with self.state.store.engine.connect() as connection:
            pending = connection.execute(
                select(dispatch.c.build_id, dispatch.c.job_id)
                .join(builds, builds.c.id == dispatch.c.build_id)
                .where(dispatch.c.status == "dispatched", builds.c.status == "admitted")
            ).all()
        for build_id, job_id in pending:
            job = self.state.store.get_job(job_id)
            if job is None or job.status not in {"completed", "failed", "timed_out"}:
                continue
            with self.state.store.engine.connect() as connection:
                run_id = connection.execute(
                    select(runs_table.c.id)
                    .where(runs_table.c.job_id == job_id)
                    .order_by(runs_table.c.started_at.desc())
                    .limit(1)
                ).scalar_one_or_none()
            if run_id is None:
                continue
            run = self.state.store.get_run(run_id)
            if run.result is None:
                continue
            self.result_verifier(job, run.result)
            self.state.finish_build(
                build_id, run.result.model_dump(mode="json"), job_id=job_id, run_id=run_id
            )

    def reconcile_operations(self):
        from goblin_king.pipeline_operations import ReleaseOperations
        from goblin_king.store_schema import pipeline_environment_states_table as environments
        from goblin_king.store_schema import pipeline_operations_table as operations

        manager = ReleaseOperations(self.state)
        with self.state.store.engine.connect() as connection:
            release_ids = connection.execute(select(environments.c.key)).scalars().all()
        for key in release_ids:
            with self.state.store.engine.connect() as connection:
                row = self.state.row(connection, environments, key)
                registration = self.state.row(connection, registrations, row["registration_key"])
                doc = row["document"]
                build = (
                    self.state.row(connection, builds, doc["desired_build"])
                    if doc["desired_build"]
                    else None
                )
            if (
                registration["enabled"]
                and doc["active"]
                and not doc["hold"]
                and not doc["operation_id"]
                and build
                and build["status"] == "published"
                and (doc["observed"] or {}).get("build_id") != doc["desired_build"]
            ):
                self.state.begin_operation(
                    key,
                    expected_version=row["version"],
                    action="deploy",
                    actor="pipeline",
                    idempotency_key="automatic-" + doc["desired_build"],
                    automatic=True,
                )
        with self.state.store.engine.connect() as connection:
            ids = (
                connection.execute(
                    select(operations.c.id).where(
                        operations.c.status.in_(["pending", "queued", "running"])
                    )
                )
                .scalars()
                .all()
            )
        for operation_id in ids:
            with self.state.store.engine.connect() as connection:
                op = self.state.row(connection, operations, operation_id)
                release = self.state.row(connection, environments, op["release_key"])
            if op["status"] == "pending":
                try:
                    manager.prepare(
                        operation_id,
                        self.operation_binding(release["document"]["environment"]["name"]),
                    )
                except PipelineConflict as error:
                    with self.state.transaction() as connection:
                        current = self.state.row(connection, operations, operation_id)
                        if current["status"] == "pending":
                            release = self.state.row(
                                connection, environments, current["release_key"]
                            )
                            self.state.cas(
                                connection,
                                operations,
                                current,
                                current["document"] | {"error": str(error)},
                                status="failed",
                            )
                            self.state.cas(
                                connection,
                                environments,
                                release,
                                release["document"] | {"operation_id": None},
                            )
                            self.state.audit(
                                connection,
                                release["project_id"],
                                operation_id,
                                "operation.rejected",
                                {"reason": str(error)},
                            )
                continue
            job = self.state.store.get_job(op["document"]["job_id"])
            if job is None or job.status not in {"completed", "failed", "timed_out"}:
                continue
            with self.state.store.engine.connect() as connection:
                run_id = connection.execute(
                    select(runs_table.c.id)
                    .where(runs_table.c.job_id == job.id)
                    .order_by(runs_table.c.started_at.desc())
                    .limit(1)
                ).scalar_one_or_none()
            run = self.state.store.get_run(run_id) if run_id else None
            if run and run.result:
                self.operation_verifier(job, run.result)
                manager.finish(operation_id, run.result.model_dump(mode="json"))

    def record_releases(self):
        import time

        from goblin_king.pipeline_recording import ReleaseRecorder
        from goblin_king.store_schema import pipeline_environment_states_table as environments
        from goblin_king.store_schema import pipeline_operations_table as operations

        recorder = ReleaseRecorder(
            self.provider, self.settings.release_repository, self.settings.release_repository_id
        )
        with self.state.store.engine.connect() as connection:
            ids = (
                connection.execute(
                    select(operations.c.id).where(operations.c.status == "recording")
                )
                .scalars()
                .all()
            )
        for operation_id in ids:
            with self.state.store.engine.connect() as connection:
                op = self.state.row(connection, operations, operation_id)
                release = self.state.row(connection, environments, op["release_key"])
                build = self.state.row(
                    connection, builds, release["document"]["observed"]["build_id"]
                )
            if op["document"].get("record_after", 0) > time.time():
                continue
            observed = release["document"]["observed"]
            request = build["document"]["request"]
            record = {
                **observed,
                "operation_id": operation_id,
                "project": release["project_id"],
                "service": request["registration"]["service_id"],
                "environment": release["document"]["environment"],
                "source_revision": request["source"]["commit"],
                "pipeline_revision": request["registration"]["pipeline_revision"],
                "configuration_revision": request["registration"]["catalog_revision"],
                "chart_version": "http-service-v1/0.1.0",
                "deployment_image": self.settings.deploy_image,
                "hold": release["document"]["hold"],
                "simulated_provider": self.settings.simulated_provider,
                "operation_outcome": op["document"].get("terminal_status", "completed"),
            }
            with self.state.transaction() as connection:
                current = self.state.row(connection, operations, operation_id)
                if "record_document" not in current["document"]:
                    self.state.cas(
                        connection,
                        operations,
                        current,
                        current["document"] | {"record_document": record},
                    )
                else:
                    record = current["document"]["record_document"]
            try:
                evidence = recorder.record(op["release_key"], operation_id, record)
            except Exception as error:
                with self.state.transaction() as connection:
                    current = self.state.row(connection, operations, operation_id)
                    count = current["document"].get("record_attempts", 0) + 1
                    self.state.cas(
                        connection,
                        operations,
                        current,
                        current["document"]
                        | {
                            "record_attempts": count,
                            "record_after": time.time() + min(300, 2 ** min(count, 9)),
                            "record_error": str(error),
                        },
                    )
                continue
            with self.state.transaction() as connection:
                current = self.state.row(connection, operations, operation_id)
                release = self.state.row(connection, environments, op["release_key"])
                if (
                    release["document"]["operation_id"] != operation_id
                    or current["status"] != "recording"
                ):
                    raise PipelineConflict("recording no longer owns release state")
                self.state.cas(
                    connection,
                    operations,
                    current,
                    current["document"] | {"recording": evidence, "record_error": None},
                    status=current["document"].get("terminal_status", "completed"),
                )
                self.state.cas(
                    connection,
                    environments,
                    release,
                    release["document"] | {"operation_id": None, "recording": evidence},
                )
                self.state.audit(
                    connection, release["project_id"], operation_id, "release.recorded", evidence
                )
