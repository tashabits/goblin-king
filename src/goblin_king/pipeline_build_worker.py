"""Trusted orchestration of credential-free tests and disposable rootless builders."""

from __future__ import annotations

import json
import os
from pathlib import Path

from kubernetes.client import ApiException

from goblin_king.contracts import GoblinResult
from goblin_king.pipeline_contracts import PipelineBuildRequest, PipelineServiceContract
from goblin_king.pipeline_kubernetes import RecoveryHold, RetainedExecution, restricted_job
from goblin_king.pipeline_sandbox import builder_job
from goblin_king.runtime_helpers import current_kubernetes_namespace, kubernetes_clients


def trusted_result(observation):
    if not observation["succeeded"]:
        raise ValueError(
            "pipeline stage failed; retained Job and Pod logs contain failure evidence"
        )
    logs = list(observation["logs"].values())
    if len(logs) != 1:
        raise RecoveryHold("expected exactly one terminal helper result")
    return json.loads(logs[0].strip().splitlines()[-1])


def build(payload):
    request = PipelineBuildRequest.model_validate(payload["request"])
    contract = PipelineServiceContract.model_validate(payload["contract"])
    batch, core = kubernetes_clients()
    config = json.loads(
        core.read_namespaced_config_map(
            "pipeline-worker-config", current_kubernetes_namespace()
        ).data["config.json"]
    )
    route = config["sources"][request.source.repository]
    namespace, node = route["namespace"], route["node"]
    helper = config["helper_image"]
    if request.environment.image_repository not in route["image_repositories"]:
        raise ValueError("publication destination is outside this worker configuration")
    ledger = RetainedExecution(
        core,
        batch,
        namespace,
        request.build_id,
        {"request": payload, "configuration_revision": config["revision"]},
    )
    if ledger.document["result"]:
        return GoblinResult.model_validate(ledger.document["result"])
    labels = {
        "org.opencontainers.image.revision": request.source.commit,
        "io.goblin-king.build-id": request.build_id,
        "io.goblin-king.pipeline-revision": request.registration.pipeline_revision,
    }
    evidence = {}

    def input_volume(stage, document):
        name = f"{request.build_id}-{stage}-input"
        raw = json.dumps(document, sort_keys=True)
        try:
            core.create_namespaced_config_map(
                namespace,
                {
                    "apiVersion": "v1",
                    "kind": "ConfigMap",
                    "metadata": {"name": name},
                    "data": {"step.json": raw},
                },
            )
        except ApiException as error:
            if error.status != 409:
                raise
            if core.read_namespaced_config_map(name, namespace).data["step.json"] != raw:
                raise RecoveryHold("stage input changed") from None
        return {"name": "input", "configMap": {"name": name}}

    try:
        for stage, target in (("test", contract.test_target), ("runtime", contract.runtime_target)):
            claim = f"build-{request.build_id}-{stage}"
            try:
                core.create_namespaced_persistent_volume_claim(
                    namespace,
                    {
                        "apiVersion": "v1",
                        "kind": "PersistentVolumeClaim",
                        "metadata": {
                            "name": claim,
                            "labels": {"goblin-king.io/build": request.build_id},
                        },
                        "spec": {
                            "accessModes": ["ReadWriteOnce"],
                            "resources": {"requests": {"storage": "8Gi"}},
                        },
                    },
                )
            except ApiException as error:
                if error.status != 409:
                    raise
                pvc = core.read_namespaced_persistent_volume_claim(claim, namespace)
                if pvc.metadata.labels.get("goblin-king.io/build") != request.build_id:
                    raise RecoveryHold("scratch volume is not owned by this build") from None
            scratch = {"name": "scratch", "persistentVolumeClaim": {"claimName": claim}}
            checkout_document = {
                "request": payload["request"],
                "contract": payload["contract"],
                "repository_id": payload["repository_id"],
                "config": config["provider"],
            }
            checkout_name = f"{request.build_id}-{stage}-checkout"
            job = restricted_job(
                checkout_name,
                namespace,
                helper,
                ["python", "-m", "goblin_king.pipeline_steps", "checkout"],
                node=node,
                volumes=[
                    scratch,
                    input_volume(stage + "-checkout", checkout_document),
                    {"name": "github", "secret": {"secretName": route["github_secret"]}},
                ],
                mounts=[
                    {"name": "scratch", "mountPath": "/scratch"},
                    {"name": "input", "mountPath": "/input", "readOnly": True},
                    {"name": "github", "mountPath": "/github", "readOnly": True},
                ],
            )
            evidence[stage + "-checkout"] = trusted_result(
                ledger.job(stage + "-checkout", job, timeout=300)
            )
            # Destroy the credentialed checkout process before source execution starts.
            ledger.remove_sandbox(stage + "-checkout")
            job = builder_job(
                name=f"{request.build_id}-{stage}-build",
                namespace=namespace,
                claim=claim,
                node=node,
                target=target,
                context=contract.context,
                dockerfile=contract.dockerfile,
                labels=labels,
            )
            built = ledger.job(stage + "-build", job, timeout=1800)
            # Even a failed build must be destroyed before any credentialed helper can run.
            ledger.remove_sandbox(stage + "-build")
            if not built["succeeded"]:
                raise ValueError(f"{stage} image build failed")
            repository = (
                route["staging_repository"]
                if stage == "test"
                else request.environment.image_repository.split("/", 1)[1]
            )
            publication = {
                "endpoint": config["registry_endpoint"],
                "repository": repository,
                "tag": request.build_id,
                "labels": labels,
            }
            job = restricted_job(
                f"{request.build_id}-{stage}-publish",
                namespace,
                helper,
                ["python", "-m", "goblin_king.pipeline_steps", "publish"],
                node=node,
                volumes=[
                    scratch,
                    input_volume(stage + "-publish", publication),
                    {"name": "registry", "secret": {"secretName": route["publisher_secret"]}},
                ],
                mounts=[
                    {"name": "scratch", "mountPath": "/scratch", "readOnly": True},
                    {"name": "input", "mountPath": "/input", "readOnly": True},
                    {"name": "registry", "mountPath": "/registry", "readOnly": True},
                ],
            )
            published = trusted_result(ledger.job(stage + "-publish", job, timeout=300))
            evidence[stage + "-publication"] = published
            if stage == "test":
                image = f"{config['registry_host']}/{repository}@{published['digest']}"
                job = restricted_job(
                    f"{request.build_id}-tests",
                    namespace,
                    image,
                    list(contract.test_command),
                    node=node,
                    timeout=900,
                    trust="source-test",
                    pull_secret=route["pull_secret"],
                )
                tests = ledger.job("tests", job, timeout=900)
                if not tests["succeeded"]:
                    raise ValueError("source tests failed")
                evidence["tests"] = {"job_uid": tests["job_uid"], "pods": tests["pods"]}
        result = GoblinResult.ok(
            data={
                "build_id": request.build_id,
                "source_revision": request.source.commit,
                "pipeline_revision": request.registration.pipeline_revision,
                "image_digest": evidence["runtime-publication"]["digest"],
                "image_repository": request.environment.image_repository,
                "evidence": evidence,
                "ledger": {"namespace": namespace, "name": ledger.name},
            }
        )
    except RecoveryHold:
        raise  # Ambiguous effects remain retained; another worker must not replace them blindly.
    except ValueError as error:
        result = GoblinResult.failed(
            error=str(error), data={"build_id": request.build_id, "evidence": evidence}
        )
    ledger.finish(result.model_dump(mode="json"))
    return result


def main():
    payload = json.loads(Path(os.environ["GOBLIN_INPUT_PATH"]).read_text())
    try:
        result = (
            GoblinResult.ok(data={"validated": True})
            if payload == {"validate": True}
            else build(payload)
        )
    except RecoveryHold as error:
        result = GoblinResult.failed(error=str(error), data={"recovery_hold": True})
    except Exception as error:
        result = GoblinResult.failed(
            error=f"trusted worker failed: {type(error).__name__}", data={"recovery_hold": True}
        )
    raw = result.model_dump_json()
    Path(os.environ["GOBLIN_RESULT_PATH"]).write_text(raw)
    # The retained runtime captures this trusted envelope independently of Redis expiry.
    print(raw, flush=True)


if __name__ == "__main__":
    main()
