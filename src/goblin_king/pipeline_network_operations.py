"""Schedule networking under the existing durable release ownership boundary."""

from uuid import NAMESPACE_URL, uuid5

from goblin_king.contracts import JobRecord, utc_now
from goblin_king.pipeline_state import PipelineConflict
from goblin_king.store_schema import pipeline_dispatch_table as dispatch
from goblin_king.store_schema import pipeline_environment_states_table as releases
from goblin_king.store_schema import pipeline_networking_table as networking
from goblin_king.store_schema import pipeline_operations_table as operations


class NetworkOperations:
    def __init__(self, state, policy):
        self.state, self.policy = state, policy

    def prepare(self, operation_id, binding):
        state = self.state
        with state.transaction() as connection:
            operation = state.row(connection, operations, operation_id)
            if not operation or operation["status"] != "networking-pending":
                return
            release = state.row(connection, releases, operation["release_key"])
            network = state.row(connection, networking, release["key"])
            if release["document"]["operation_id"] != operation_id:
                raise PipelineConflict("networking no longer owns the release")
            if network["document"]["policy_revision"] != self.policy.revision:
                raise PipelineConflict("installed network policy changed before execution")
            environment = release["document"]["environment"]
            observed = release["document"].get("observed")
            payload = {
                "operation_id": operation_id,
                "release_key": release["key"],
                "project_id": release["project_id"],
                "environment": environment,
                "generation": release["document"]["generation"],
                "configuration": network["document"]["desired"],
                "policy_revision": self.policy.revision,
                "active": bool(observed and release["document"]["active"]),
                "port": observed.get("port", 8080) if observed else 8080,
                "health_path": observed.get("health_path", "/health") if observed else "/health",
                "previous": network["document"].get("observed"),
            }
            job_id = str(uuid5(NAMESPACE_URL, "goblin-king/networking/" + operation_id))
            job = JobRecord(
                id=job_id,
                kind="pipeline.network." + environment["name"],
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
                "networking.queued",
                {"job_id": job_id},
            )

    def finish(self, operation_id, result):
        state = self.state
        with state.transaction() as connection:
            operation = state.row(connection, operations, operation_id)
            release = state.row(connection, releases, operation["release_key"])
            network = state.row(connection, networking, release["key"])
            if operation["document"].get("result"):
                if operation["document"]["result"] != result:
                    raise PipelineConflict("networking result conflicts")
                return
            if release["document"]["operation_id"] != operation_id:
                raise PipelineConflict("networking lost release ownership")
            success = result["status"] == "success" and not result.get("data", {}).get(
                "recovery_hold"
            )
            updated = release["document"].copy()
            if success:
                observed = result["data"].get("networking", {})
                if (
                    observed.get("configuration") != network["document"]["desired"]
                    or observed.get("policy_revision") != network["document"]["policy_revision"]
                    or observed.get("active") != operation["document"]["execution"]["active"]
                ):
                    raise PipelineConflict("observed networking differs from approved execution")
                state.cas(
                    connection, networking, network, network["document"] | {"observed": observed}
                )
                if (
                    operation["document"]["execution"].get("recover")
                    and (updated.get("hold") or {}).get("operation_id") == operation_id
                    and updated["hold"].get("reason") == "ambiguous networking execution"
                ):
                    # Explicit recovery has verified this operation's actual effects.
                    updated["hold"] = None
                if updated["observed"]:
                    updated["observed"] = updated["observed"] | {"networking": observed}
                    status = "recording"
                else:
                    updated["operation_id"] = None
                    status = "completed"
            else:
                updated["hold"] = {
                    "reason": "ambiguous networking execution",
                    "operation_id": operation_id,
                }
                status = "held"
            state.cas(connection, releases, release, updated)
            state.cas(
                connection,
                operations,
                operation,
                operation["document"] | {"result": result},
                status=status,
            )
            state.audit(
                connection,
                release["project_id"],
                operation_id,
                "networking." + status,
                {"result": result},
            )
