"""Authenticated delivery routes sharing the same service layer as the CLI."""

from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from goblin_king.pipeline_contracts import PipelineRegistration
from goblin_king.pipeline_github import ProviderUnavailable, UnapprovedSource
from goblin_king.pipeline_service import PipelineService
from goblin_king.pipeline_state import PipelineConflict
from goblin_king.store_schema import (
    pipeline_builds_table,
    pipeline_environment_states_table,
    pipeline_operations_table,
    pipeline_registrations_table,
)


class DiscoverRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    registration: PipelineRegistration
    repository_id: int = Field(gt=0, strict=True)


class AttachRequest(DiscoverRequest):
    expected_version: int = Field(ge=0, strict=True)
    idempotency_key: str = Field(min_length=1, max_length=200)


class MutationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1, strict=True)
    idempotency_key: str = Field(min_length=1, max_length=200)


class ManagementRequest(MutationRequest):
    enabled: bool = Field(strict=True)


class ReleaseRequest(MutationRequest):
    action: str
    payload: dict = Field(default_factory=dict)


def install_pipeline_api(app, app_state, require_principal, project_for_request):
    router = APIRouter(prefix="/pipeline", tags=["pipeline"])
    service = (
        PipelineService(app_state.store, app_state.settings.pipeline)
        if app_state.settings.pipeline.enabled
        else None
    )
    app.state.pipeline = service
    principal_dependency = Depends(require_principal)
    if service and app_state.resource_policies is not None:
        service.settings.worker_policy = app_state.resource_policies.effective_for(
            "pipeline.build", timeout_seconds=2700, max_retries=0
        )

    def validate_dispatch(job):
        from goblin_king.validation import VALIDATOR_VERSION
        from goblin_king.versions import GOBLIN_CONTAINER_CONTRACT_VERSION

        worker = app_state.workers.get(job.kind)
        binding = job.metadata["pipeline"]
        runtime = app_state.settings.kubernetes_runtime
        if (
            worker.image != binding["image"]
            or runtime.workload_security_profile != binding["profile"]
        ):
            raise PipelineConflict("approved worker image or execution profile changed")
        if app_state.resource_policies is None:
            raise PipelineConflict("pipeline requires configured resource policies")
        policy = app_state.resource_policies.effective_for(
            job.kind, timeout_seconds=job.timeout_seconds, max_retries=job.max_retries
        )
        if policy.compact() != job.metadata["resource_policy"]:
            raise PipelineConflict("approved resource policy changed")
        proof = app_state.store.get_latest_worker_validation(
            kind=job.kind,
            image_digest=runtime.validation_image_identity(worker.image, job.kind),
            contract_version=GOBLIN_CONTAINER_CONTRACT_VERSION,
            validator_version=VALIDATOR_VERSION,
        )
        if proof is None or proof.status != "passed":
            raise PipelineConflict("validate the pinned pipeline worker before dispatch")

    if service:
        service.dispatch_validator = validate_dispatch

        def verify_configuration(registration):
            import json

            from goblin_king.contracts import JobRecord, utc_now
            from goblin_king.pipeline_settings import verify_installed_routes
            from goblin_king.runtime_helpers import current_kubernetes_namespace, kubernetes_clients

            _, core = kubernetes_clients()
            config = json.loads(
                core.read_namespaced_config_map(
                    "pipeline-worker-config", current_kubernetes_namespace()
                ).data["config.json"]
            )
            verify_installed_routes(registration, config)
            for kind in {
                "pipeline.build",
                *("pipeline.deploy." + route.name for route in registration.environments),
            }:
                timeout = 2700 if kind == "pipeline.build" else 900
                image = (
                    service.settings.worker_image
                    if kind == "pipeline.build"
                    else service.settings.deploy_image
                )
                policy = app_state.resource_policies.effective_for(
                    kind, timeout_seconds=timeout, max_retries=0
                )
                validate_dispatch(
                    JobRecord(
                        id="onboarding-validation",
                        created_at=utc_now(),
                        kind=kind,
                        timeout_seconds=timeout,
                        max_retries=0,
                        metadata={
                            "pipeline": {
                                "image": image,
                                "profile": service.settings.worker_profile,
                            },
                            "resource_policy": policy.compact(),
                        },
                    )
                )

        service.configuration_verifier = verify_configuration

        def acknowledge_execution(job_id, result):
            import json
            from uuid import NAMESPACE_URL, uuid5

            from goblin_king.contracts import utc_now
            from goblin_king.runtime_helpers import current_kubernetes_namespace, kubernetes_clients

            _, core = kubernetes_clients()
            namespace = current_kubernetes_namespace()
            name = "pipeline-" + str(uuid5(NAMESPACE_URL, "goblin-king/execution/" + job_id))
            record = core.read_namespaced_config_map(name, namespace)
            document = json.loads(record.data["execution.json"])
            if document["identity"]["job_id"] != job_id or document["result"] != result:
                raise PipelineConflict(
                    "retained execution differs from the durable acknowledgement"
                )
            if not document.get("acknowledged"):
                document.update(acknowledged=True, acknowledged_at=utc_now().isoformat())
                record.data["execution.json"] = json.dumps(document, sort_keys=True)
                core.replace_namespaced_config_map(name, namespace, record)

        service.execution_acknowledger = acknowledge_execution

        def verify_execution(job, result):
            import json

            from goblin_king.runtime_helpers import kubernetes_clients

            if result.status != "success":
                return
            batch, core = kubernetes_clients()
            execution = result.data["execution"]
            record = core.read_namespaced_config_map(execution["name"], execution["namespace"])
            retained = json.loads(record.data["execution.json"])
            if retained["identity"]["job_id"] != job.id or retained["result"] != result.model_dump(
                mode="json"
            ):
                raise PipelineConflict("scheduler result differs from retained execution evidence")
            identity = retained["stages"]["worker"]
            observed = batch.read_namespaced_job(identity["name"], execution["namespace"])
            if observed.metadata.uid != identity["uid"] or not observed.status.succeeded:
                raise PipelineConflict("execution Job identity or completion cannot be verified")

        if service.settings.deploy_image:

            def operation_binding(environment):
                kind = "pipeline.deploy." + environment
                worker = app_state.workers.get(kind)
                if worker.image != service.settings.deploy_image:
                    raise PipelineConflict("deployment image differs from installed approval")
                return {
                    "image": worker.image,
                    "profile": service.settings.worker_profile,
                    "resource_policy": app_state.resource_policies.effective_for(
                        kind, timeout_seconds=900, max_retries=0
                    ).compact(),
                }

            service.operation_binding = operation_binding
            service.operation_verifier = verify_execution

        def verify_build_result(job, result):
            import hashlib
            import json
            from pathlib import Path

            from goblin_king.pipeline_oci import RegistryClient
            from goblin_king.runtime_helpers import kubernetes_clients

            if result.status != "success":
                return
            verify_execution(job, result)
            from goblin_king.pipeline_contracts import PipelineBuildRequest

            approved = PipelineBuildRequest.model_validate(job.input["request"])
            if result.data.get("image_repository") != approved.environment.image_repository:
                raise PipelineConflict("publication destination differs from admission")
            batch, core = kubernetes_clients()
            execution = result.data["execution"]
            record = core.read_namespaced_config_map(execution["name"], execution["namespace"])
            retained = json.loads(record.data["execution.json"])
            if retained["result"] != result.model_dump(mode="json"):
                raise PipelineConflict("scheduler result differs from retained worker evidence")
            identity = retained["stages"]["worker"]
            observed = batch.read_namespaced_job(identity["name"], execution["namespace"])
            if observed.metadata.uid != identity["uid"] or not observed.status.succeeded:
                raise PipelineConflict("worker Job identity or completion cannot be verified")
            root = Path(service.settings.registry_reader_path)
            client = RegistryClient(
                service.settings.registry_endpoint,
                (root / "username").read_text(),
                (root / "password").read_text(),
                root / "ca.crt",
            )
            repository = result.data["image_repository"].split("/", 1)[1]
            digest = result.data["image_digest"]
            _, _, raw = client.request(f"/v2/{repository}/manifests/{digest}")
            if hashlib.sha256(raw).hexdigest() != digest.removeprefix("sha256:"):
                raise PipelineConflict("registry digest does not match the worker result")
            descriptor = json.loads(raw)["config"]
            _, _, config = client.request(f"/v2/{repository}/blobs/{descriptor['digest']}")
            if hashlib.sha256(config).hexdigest() != descriptor["digest"].removeprefix("sha256:"):
                raise PipelineConflict("registry configuration checksum failed")
            labels = json.loads(config).get("config", {}).get("Labels", {})
            if (
                labels.get("org.opencontainers.image.revision")
                != job.input["request"]["source"]["commit"]
            ):
                raise PipelineConflict("registry source revision differs from admission")

        service.result_verifier = verify_build_result

    def active():
        if service is None:
            raise HTTPException(503, "pipeline management is not configured")
        return service

    def invoke(function, *args, **kwargs):
        try:
            return function(*args, **kwargs)
        except KeyError:
            raise HTTPException(404, "pipeline resource not found") from None
        except PipelineConflict as error:
            raise HTTPException(409, str(error)) from None
        except UnapprovedSource as error:
            raise HTTPException(422, str(error)) from None
        except ValueError as error:
            raise HTTPException(422, str(error)) from None
        except ProviderUnavailable as error:
            raise HTTPException(503, str(error)) from None

    def authorize(principal, project, *, mutation=False):
        if mutation and principal.role not in {"admin", "member"}:
            raise HTTPException(403, "delivery mutations require project member or admin access")
        project_for_request(principal, project)
        if not project:
            raise HTTPException(422, "an explicit project is required")

    @router.get("/status")
    def status(principal=principal_dependency):
        return {
            "enabled": service is not None,
            "simulated_provider": app_state.settings.pipeline.simulated_provider,
            "recovery_mode": app_state.settings.pipeline.recovery_mode,
            "last_error": service.last_error if service else None,
        }

    @router.get("/recovery")
    def recovery_inventory(principal=principal_dependency):
        import json

        from sqlalchemy import select

        if not principal.is_admin:
            raise HTTPException(403, "installation recovery requires administrator access")
        result = {"recovery_mode": active().settings.recovery_mode}
        with app_state.store.engine.connect() as connection:
            for name, table in (
                ("releases", pipeline_environment_states_table),
                ("operations", pipeline_operations_table),
                ("builds", pipeline_builds_table),
            ):
                result[name] = [
                    dict(row) | {"document": json.loads(row["document"])}
                    for row in connection.execute(select(table)).mappings()
                ]
        return result

    @router.post("/repositories/discover")
    def discover(body: DiscoverRequest, principal=principal_dependency):
        authorize(principal, body.registration.project_id, mutation=True)
        return invoke(active().discover, body.registration, body.repository_id)

    @router.post("/repositories", status_code=201)
    def attach(body: AttachRequest, principal=principal_dependency):
        authorize(principal, body.registration.project_id, mutation=True)
        return invoke(
            active().attach,
            body.registration,
            body.repository_id,
            body.expected_version,
            principal.user_id,
            body.idempotency_key,
        )

    @router.get("/repositories")
    def repositories(project_id: str, principal=principal_dependency):
        authorize(principal, project_id)
        return {"items": active().state.list(pipeline_registrations_table, project_id=project_id)}

    @router.post("/repositories/{key}/management")
    def management(key: str, body: ManagementRequest, principal=principal_dependency):
        row = invoke(active().get_registration, key)
        authorize(principal, row["project_id"], mutation=True)
        return invoke(
            active().management,
            key,
            body.expected_version,
            body.enabled,
            principal.user_id,
            body.idempotency_key,
        )

    @router.get("/builds")
    def builds(project_id: str, principal=principal_dependency):
        authorize(principal, project_id)
        return {"items": active().state.list(pipeline_builds_table, project_id=project_id)}

    @router.get("/releases")
    def releases(project_id: str, principal=principal_dependency):
        authorize(principal, project_id)
        return {
            "items": active().state.list(pipeline_environment_states_table, project_id=project_id)
        }

    @router.post("/builds/{build_id}/rebuild", status_code=202)
    def rebuild(build_id: str, body: MutationRequest, principal=principal_dependency):
        with app_state.store.engine.connect() as connection:
            row = active().state.row(connection, pipeline_builds_table, build_id)
        if row is None:
            raise HTTPException(404, "build not found")
        authorize(principal, row["project_id"], mutation=True)
        return invoke(
            active().state.rebuild,
            build_id,
            expected_version=body.expected_version,
            actor=principal.user_id,
            idempotency_key=body.idempotency_key,
        )

    @router.get("/operations")
    def operations(project_id: str, principal=principal_dependency):
        authorize(principal, project_id)
        return {"items": active().state.list(pipeline_operations_table, project_id=project_id)}

    @router.post("/releases/{key}/operations", status_code=202)
    def release_action(key: str, body: ReleaseRequest, principal=principal_dependency):
        with app_state.store.engine.connect() as connection:
            row = active().state.row(connection, pipeline_environment_states_table, key)
        if row is None:
            raise HTTPException(404, "release not found")
        authorize(principal, row["project_id"], mutation=True)
        return invoke(
            active().state.begin_operation,
            key,
            expected_version=body.expected_version,
            action=body.action,
            actor=principal.user_id,
            idempotency_key=body.idempotency_key,
            payload=body.payload,
        )

    @router.post("/operations/{operation_id}/recover", status_code=202)
    def recover_operation(operation_id: str, body: MutationRequest, principal=principal_dependency):
        from goblin_king.pipeline_identity import terminated_execution
        from goblin_king.pipeline_operations import ReleaseOperations
        from goblin_king.runtime_helpers import current_kubernetes_namespace, kubernetes_clients

        with app_state.store.engine.connect() as connection:
            operation = active().state.row(connection, pipeline_operations_table, operation_id)
        if operation is None:
            raise HTTPException(404, "operation not found")
        authorize(principal, operation["project_id"], mutation=True)
        manager = ReleaseOperations(active().state)
        replay = invoke(
            manager.recovery_receipt,
            operation_id,
            body.expected_version,
            principal.user_id,
            body.idempotency_key,
        )
        if replay:
            return replay
        if active().settings.recovery_mode or active().operation_binding is None:
            raise HTTPException(
                409, "delivery is held in recovery mode or execution is unavailable"
            )
        if operation["status"] != "held" or not operation["document"].get("execution"):
            raise HTTPException(409, "only interrupted release executions can be reconciled")
        batch, core = kubernetes_clients()
        termination = invoke(
            terminated_execution, operation, core, batch, current_kubernetes_namespace()
        )
        return invoke(
            manager.recover,
            operation_id,
            expected_version=body.expected_version,
            actor=principal.user_id,
            termination=termination,
            idempotency_key=body.idempotency_key,
            binding=active().operation_binding(
                operation["document"]["execution"]["environment"]["name"]
            ),
        )

    @router.post("/webhooks/github", status_code=202)
    async def webhook(request: Request):
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 1024 * 1024:
                raise HTTPException(413, "webhook exceeds maximum body size")
        return invoke(
            active().receive,
            bytes(raw),
            request.headers.get("X-Hub-Signature-256", ""),
            request.headers.get("X-GitHub-Delivery", ""),
            request.headers.get("X-GitHub-Event", ""),
        )

    @router.post("/internal/operations/{operation_id}/claim")
    def claim_operation(operation_id: str, request: Request):
        from goblin_king.pipeline_identity import verify_execution_identity
        from goblin_king.pipeline_operations import ReleaseOperations
        from goblin_king.runtime_helpers import current_kubernetes_namespace, kubernetes_clients

        state = active().state
        if service.settings.recovery_mode:
            raise HTTPException(409, "delivery is held in recovery mode")
        with state.store.engine.connect() as connection:
            operation = state.row(connection, pipeline_operations_table, operation_id)
        if operation is None:
            raise HTTPException(404, "operation not found")
        _, core = kubernetes_clients()
        pod_uid = invoke(
            verify_execution_identity,
            request.headers.get("authorization", "").removeprefix("Bearer "),
            operation,
            core,
            current_kubernetes_namespace(),
        )
        return invoke(ReleaseOperations(state).claim, operation_id, pod_uid)

    app.include_router(router)
    if service:
        previous_lifespan = app.router.lifespan_context

        @asynccontextmanager
        async def lifespan(application):
            async with previous_lifespan(application) as lifespan_state:
                service.start()
                try:
                    yield lifespan_state
                finally:
                    service.close()

        app.router.lifespan_context = lifespan
