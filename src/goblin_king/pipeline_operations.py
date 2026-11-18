"""Durable release selection, exclusive execution claims, and observed-state transitions."""

from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import select

from goblin_king.contracts import JobRecord, utc_now
from goblin_king.pipeline_networking import proxy_environment
from goblin_king.pipeline_state import PipelineConflict, encoded
from goblin_king.pipeline_store import _fingerprint
from goblin_king.store_schema import pipeline_actions_table as actions
from goblin_king.store_schema import (
    pipeline_builds_table as builds,
)
from goblin_king.store_schema import (
    pipeline_dispatch_table as dispatch,
)
from goblin_king.store_schema import (
    pipeline_environment_states_table as releases,
)
from goblin_king.store_schema import pipeline_networking_table as networking
from goblin_king.store_schema import (
    pipeline_operations_table as operations,
)


class ReleaseOperations:
    def __init__(self, state, *, network_policy=None, network_binding=None):
        self.state = state
        self.network_policy, self.network_binding = network_policy, network_binding

    def prepare(self, operation_id, binding):
        """Select only a verified digest and persist its exact execution intent atomically."""
        state = self.state
        with state.transaction() as connection:
            operation = state.row(connection, operations, operation_id)
            if operation is None:
                raise KeyError(operation_id)
            if operation["status"] != "pending":
                return
            release = state.row(connection, releases, operation["release_key"])
            document = release["document"]
            if document["operation_id"] != operation_id:
                raise PipelineConflict("operation no longer owns the release")
            action = operation["document"]["action"]
            if action in {"record", "clear-hold"}:
                if (
                    action == "clear-hold"
                    and document["hold"]
                    and document["hold"].get("reason")
                    not in {
                        "emergency rollback",
                        "recovered release awaiting explicit hold clearance",
                    }
                ):
                    raise PipelineConflict(
                        "ambiguous recovery requires termination and state reconciliation"
                    )
                if not document["observed"]:
                    raise PipelineConflict("no observed release to record")
                changed = document | {"hold": None} if action == "clear-hold" else document
                state.cas(connection, releases, release, changed)
                state.cas(
                    connection,
                    operations,
                    operation,
                    operation["document"]
                    | {"execution": {"build_id": document["observed"]["build_id"]}},
                    status="recording",
                )
                return
            previous = document["observed"]
            selected = document["desired_build"]
            if action == "activate":
                candidates = [
                    state.row(connection, builds, item)
                    for item in connection.execute(
                        select(builds.c.id).where(
                            builds.c.project_id == release["project_id"],
                            builds.c.status.in_(["published", "superseded"]),
                        )
                    ).scalars()
                ]
                candidates = [
                    item
                    for item in candidates
                    if item["document"]["release_key"] == operation["release_key"]
                    and (item["document"].get("result") or {}).get("status") == "success"
                ]
                if candidates:
                    selected = max(
                        candidates, key=lambda item: item["document"]["request"]["created_at"]
                    )["id"]
            if action == "deactivate":
                selected = previous["build_id"] if previous else None
            if action == "rollback":
                selected = operation["document"]["payload"].get("build_id")
                history = document.get("history", [])
                if not any(item["build_id"] == selected for item in history):
                    raise PipelineConflict("rollback requires a previously healthy release")
            build = state.row(connection, builds, selected) if selected else None
            if (
                build is None
                or build["status"] not in {"published", "superseded"}
                or (build["document"].get("result") or {}).get("status") != "success"
            ):
                raise PipelineConflict("release requires a verified build")
            if build["document"]["release_key"] != operation["release_key"]:
                raise PipelineConflict("build belongs to another release")
            result = build["document"]["result"]["data"]
            contract = build["document"]["verification"]["contract"]
            active = (
                False
                if action == "deactivate"
                else (True if action == "activate" else document["active"])
            )
            payload = {
                "operation_id": operation_id,
                "release_key": operation["release_key"],
                "generation": document["generation"],
                "environment": document["environment"],
                "build_id": selected,
                "previous": previous,
                "values": {
                    "image": result["image_repository"] + "@" + result["image_digest"],
                    "sourceRevision": result["source_revision"],
                    "operationId": operation_id,
                    "active": active,
                    "port": contract["port"],
                    "healthPath": contract["health_path"],
                    "backgroundCommand": contract.get("background_command") or [],
                    "scheduledCommand": contract.get("scheduled_command") or [],
                },
            }
            network = state.row(connection, networking, operation["release_key"])
            if network:
                if not self.network_policy or not self.network_binding:
                    raise PipelineConflict("configured networking execution is unavailable")
                configuration = self.network_policy.validate_configuration(
                    network["document"]["desired"],
                    identity=document["environment"]["namespace"]
                    + "/"
                    + document["environment"]["release"],
                    project_id=release["project_id"],
                    environment=document["environment"]["name"],
                )
                if network["document"]["policy_revision"] != self.network_policy.revision:
                    raise PipelineConflict("networking policy changed; preview configuration again")
                approved = self.network_binding(document["environment"]["name"])
                payload["networking"] = {
                    "configuration": configuration.model_dump(mode="json"),
                    "policy_revision": self.network_policy.revision,
                    "image": approved["image"],
                    "project_id": release["project_id"],
                    "previous": network["document"].get("observed"),
                }
                payload["values"]["proxyEnvironment"] = proxy_environment(
                    configuration,
                    document["environment"]["namespace"],
                    document["environment"]["release"],
                )
            job_id = str(uuid5(NAMESPACE_URL, "goblin-king/release/" + operation_id))
            job = JobRecord(
                id=job_id,
                kind="pipeline.deploy." + document["environment"]["name"],
                project_id=release["project_id"],
                created_at=utc_now(),
                created_by="pipeline",
                input=payload,
                timeout_seconds=900,
                max_retries=0,
                metadata={
                    "pipeline": {"operation_id": operation_id, **binding},
                    "resource_policy": binding["resource_policy"],
                },
            )
            connection.execute(
                dispatch.insert().values(
                    id=job_id,
                    build_id="",
                    job_id=job_id,
                    version=1,
                    status="pending",
                    document=job.model_dump_json(),
                )
            )
            state.cas(
                connection,
                operations,
                operation,
                operation["document"] | {"job_id": job_id, "execution": payload},
                status="queued",
            )
            state.audit(
                connection,
                release["project_id"],
                operation_id,
                "operation.queued",
                {"job_id": job_id, "build_id": selected},
            )

    def claim(self, operation_id, pod_uid):
        """Recheck generation under the release lock immediately before external effects."""
        state = self.state
        with state.transaction() as connection:
            operation = state.row(connection, operations, operation_id)
            if operation is None:
                raise KeyError(operation_id)
            release = state.row(connection, releases, operation["release_key"])
            doc, op = release["document"], operation["document"]
            if doc["operation_id"] != operation_id:
                raise PipelineConflict("release ownership changed")
            if operation["status"] == "running" and op["worker"] == pod_uid:
                return op["execution"]
            if operation["status"] != "queued" or op["worker"]:
                raise PipelineConflict("operation already claimed; termination must be reconciled")
            if (
                op["action"] == "deploy"
                and not op["execution"].get("recover")
                and (
                    doc["generation"] != op["execution"]["generation"]
                    or doc["hold"]
                    or doc["active"] != op["execution"]["values"]["active"]
                )
            ):
                state.cas(connection, operations, operation, op, status="superseded")
                state.cas(connection, releases, release, doc | {"operation_id": None})
                return {"superseded": True}
            state.cas(connection, operations, operation, op | {"worker": pod_uid}, status="running")
            state.audit(
                connection,
                release["project_id"],
                operation_id,
                "operation.claimed",
                {"pod_uid": pod_uid},
            )
            return op["execution"]

    def recovery_receipt(self, operation_id, expected_version, actor, idempotency_key):
        import json

        key = _fingerprint([operation_id, "recover", idempotency_key])
        fingerprint = _fingerprint([expected_version, actor])
        with self.state.store.engine.connect() as connection:
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

    def recover(
        self, operation_id, *, expected_version, actor, termination, binding, idempotency_key
    ):
        """Create an inspection attempt only after authoritative termination was established."""
        state = self.state
        replay = self.recovery_receipt(operation_id, expected_version, actor, idempotency_key)
        if replay:
            return replay
        with state.transaction() as connection:
            operation = state.row(connection, operations, operation_id)
            if operation is None:
                raise KeyError(operation_id)
            state.expected(operation, expected_version)
            if operation["status"] != "held" or not operation["document"].get("job_id"):
                raise PipelineConflict("only interrupted release executions can be reconciled")
            if termination.get("job_id") != operation["document"]["job_id"] or not termination.get(
                "terminated"
            ):
                raise PipelineConflict("previous execution termination is not established")
            release = state.row(connection, releases, operation["release_key"])
            if release["document"]["operation_id"] != operation_id:
                raise PipelineConflict("interrupted operation no longer owns the release")
            old = operation["document"]
            attempt = old.get("recovery_attempt", 0) + 1
            job_id = str(
                uuid5(NAMESPACE_URL, f"goblin-king/release/{operation_id}/recovery/{attempt}")
            )
            payload = old["execution"] | {"recover": True, "recovery_attempt": attempt}
            job = JobRecord(
                id=job_id,
                kind=("pipeline.network." if old["action"] == "networking" else "pipeline.deploy.")
                + payload["environment"]["name"],
                project_id=operation["project_id"],
                created_at=utc_now(),
                created_by="pipeline",
                timeout_seconds=900,
                max_retries=0,
                input=payload,
                metadata={
                    "pipeline": {"operation_id": operation_id, **binding},
                    "resource_policy": binding["resource_policy"],
                },
            )
            connection.execute(
                dispatch.insert().values(
                    id=job_id,
                    job_id=job_id,
                    build_id="",
                    version=1,
                    status="pending",
                    document=job.model_dump_json(),
                )
            )
            document = old | {
                "job_id": job_id,
                "execution": payload,
                "worker": None,
                "recovery_attempt": attempt,
                "prior_results": old.get("prior_results", []) + [old.get("result")],
                "termination": termination,
            }
            document.pop("result", None)
            state.cas(connection, operations, operation, document, status="queued")
            state.audit(
                connection,
                operation["project_id"],
                operation_id,
                "operation.recovery-requested",
                {"actor": actor, "termination": termination, "job_id": job_id},
            )
            response = {
                "operation_id": operation_id,
                "job_id": job_id,
                "version": expected_version + 1,
            }
            connection.execute(
                actions.insert().values(
                    key=_fingerprint([operation_id, "recover", idempotency_key]),
                    fingerprint=_fingerprint([expected_version, actor]),
                    response=encoded(response),
                )
            )
            return response

    def finish(self, operation_id, result):
        """Keep ambiguous ownership held; successful health awaits recording only."""
        state = self.state
        with state.transaction() as connection:
            operation = state.row(connection, operations, operation_id)
            if operation is None:
                raise KeyError(operation_id)
            op = operation["document"]
            if op.get("result") is not None:
                if op["result"] != result:
                    raise PipelineConflict("operation result conflicts")
                return
            if operation["status"] == "superseded":
                return
            release = state.row(connection, releases, operation["release_key"])
            doc = release["document"]
            if doc["operation_id"] != operation_id:
                raise PipelineConflict("operation lost its release lock")
            data = result.get("data", {})
            if result["status"] != "success" or data.get("recovery_hold"):
                updated = doc | {
                    "hold": {"reason": "ambiguous release execution", "operation_id": operation_id}
                }
                status = "held"
            else:
                observed = data.get("release")
                if observed is None:
                    raise PipelineConflict("completed operation has no observed release")
                if observed["status"] == "success":
                    expected = op["execution"]["values"]
                    if (
                        observed["image"] != expected["image"]
                        or observed["active"] != expected["active"]
                    ):
                        raise PipelineConflict(
                            "observed release differs from selected digest or activation"
                        )
                    observed = observed | {
                        "build_id": op["execution"]["build_id"],
                        "operation_id": operation_id,
                    }
                    history = doc.get("history", []) + [observed]
                    updated = doc | {
                        "observed": observed,
                        "active": observed["active"],
                        "history": history[-50:],
                    }
                    if op["execution"].get("recover"):
                        updated["hold"] = {
                            "reason": "recovered release awaiting explicit hold clearance",
                            "operation_id": operation_id,
                        }
                    if op["action"] == "rollback":
                        updated["hold"] = {
                            "reason": "emergency rollback",
                            "operation_id": operation_id,
                        }
                    status = "recording"
                else:
                    updated = doc | {"operation_id": None}
                    if observed.get("rollback"):
                        updated["observed"] = observed["rollback"]
                        updated["operation_id"] = operation_id
                        if op["execution"].get("recover"):
                            updated["hold"] = {
                                "reason": "recovered release awaiting explicit hold clearance",
                                "operation_id": operation_id,
                            }
                        op = op | {"terminal_status": "failed"}
                        status = "recording"
                    else:
                        status = "failed"
            network_result = (updated.get("observed") or {}).get("networking")
            if status == "recording" and op["execution"].get("networking"):
                expected_network = op["execution"]["networking"]
                if (
                    not network_result
                    or any(
                        network_result.get(key) != expected_network[key]
                        for key in ("configuration", "policy_revision")
                    )
                    or network_result.get("active") != updated["observed"]["active"]
                ):
                    raise PipelineConflict("release lacks verified networking evidence")
                network = state.row(connection, networking, operation["release_key"])
                state.cas(
                    connection,
                    networking,
                    network,
                    network["document"] | {"observed": network_result},
                )
            state.cas(connection, releases, release, updated)
            state.cas(connection, operations, operation, op | {"result": result}, status=status)
            state.audit(
                connection,
                release["project_id"],
                operation_id,
                "operation." + status,
                {"result": result},
            )
