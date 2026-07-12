"""Local runtime tests for in-process goblin execution."""

import subprocess
from types import SimpleNamespace

from goblin_king import kubernetes_runtime as kubernetes_runtime_module
from goblin_king.contracts import GoblinContext, GoblinDefinition, GoblinResult
from goblin_king.kubernetes_artifact_config import KubernetesArtifactRetention
from goblin_king.kubernetes_pod_diagnostics import (
    DEFAULT_KUBERNETES_LOG_CAPTURE_BYTES,
    KubernetesRunObservation,
)
from goblin_king.registry import GoblinRegistry
from goblin_king.resource_policies import ResourcePolicy
from goblin_king.runtime import (
    DockerRuntime,
    InProcessRuntime,
    KubernetesRuntime,
    _worker_env,
    _worker_secret_refs,
)
from goblin_king.versions import GOBLIN_CONTAINER_CONTRACT_VERSION
from goblin_king.workers import WorkerImageDefinition, WorkerImageMap


def test_echo_goblin_runs_successfully() -> None:
    """Verify a normal GoblinResult-returning goblin completes successfully."""
    registry = GoblinRegistry.from_path("examples/goblins.json")
    definition, entrypoint = registry.resolve("example.echo")
    context = GoblinContext(run_id="run-1", artifact_root=".goblin-king/artifacts/run-1")

    result = InProcessRuntime().run(definition, entrypoint, {"message": "hello"}, context)

    assert result.status == "success"
    assert result.data["echo"] == {"message": "hello"}
    assert result.data["run_id"] == "run-1"


def test_plain_dict_result_is_wrapped() -> None:
    """Verify goblins may return plain dictionaries for ergonomic early authoring."""
    registry = GoblinRegistry.from_path("tests/fixtures/plain-registry.json")
    definition, entrypoint = registry.resolve("example.plain")
    context = GoblinContext(run_id="run-2", artifact_root=".goblin-king/artifacts/run-2")

    result = InProcessRuntime().run(definition, entrypoint, {"value": 2}, context)

    assert result.status == "success"
    assert result.data == {"plain": {"value": 2}, "run_id": "run-2"}


def test_exceptions_become_failed_results() -> None:
    """Verify goblin exceptions are returned as structured failed envelopes."""
    registry = GoblinRegistry.from_path("tests/fixtures/failing-registry.json")
    definition, entrypoint = registry.resolve("example.fail")
    context = GoblinContext(run_id="run-3", artifact_root=".goblin-king/artifacts/run-3")

    result = InProcessRuntime().run(definition, entrypoint, {}, context)

    assert result.status == "failed"
    assert "intentional failure" in (result.error or "")


def test_kubernetes_job_includes_result_forwarder() -> None:
    """Verify Kubernetes workers can stay language-neutral by writing result.json only."""
    workers = WorkerImageMap(
        {"example.hello-go": WorkerImageDefinition(context=".", image="hello-go:local")},
        root=".",
    )
    runtime = KubernetesRuntime(
        workers=workers,
        redis_url="redis://redis:6379/0",
        namespace="default",
        result_forwarder_image="goblin-king:test",
    )
    context = GoblinContext(
        run_id="run-4",
        artifact_root=".goblin-king/artifacts/run-4",
        metadata={"job_id": "job-4"},
    )

    manifest = runtime._job_manifest(
        name="gk-example-hello-go-run-4",
        config_name="gk-example-hello-go-run-4-input",
        image="hello-go:local",
        context=context,
        worker_id="k8s-worker-run-4",
        timeout_seconds=30,
    )

    containers = manifest["spec"]["template"]["spec"]["containers"]
    names = {container["name"] for container in containers}
    assert names == {"worker", "result-forwarder"}
    worker = next(container for container in containers if container["name"] == "worker")
    assert {
        "name": "GOBLIN_CONTRACT_VERSION",
        "value": GOBLIN_CONTAINER_CONTRACT_VERSION,
    } in worker["env"]

    forwarder = next(
        container for container in containers if container["name"] == "result-forwarder"
    )
    assert forwarder["image"] == "goblin-king:test"
    assert forwarder["command"] == [
        "python",
        "-m",
        "goblin_king.kubernetes_result_forwarder",
    ]
    assert {"name": "GOBLIN_REDIS_URL", "value": "redis://redis:6379/0"} in forwarder["env"]
    assert {"name": "GOBLIN_RESULT_PATH", "value": "/goblin-result/result.json"} in forwarder[
        "env"
    ]
    assert {"name": "result", "mountPath": "/goblin-result"} in forwarder["volumeMounts"]
    assert {
        "name": "artifacts",
        "mountPath": "/artifacts",
        "readOnly": True,
    } in forwarder["volumeMounts"]


def test_kubernetes_job_retains_artifacts_on_operator_pvc() -> None:
    """Mount durable storage only into the trusted forwarder with effective policy limits."""
    workers = WorkerImageMap(
        {"example.artifact": WorkerImageDefinition(context=".", image="artifact:local")},
        root=".",
    )
    runtime = KubernetesRuntime(
        workers=workers,
        redis_url="redis://redis:6379/0",
        artifact_retention=KubernetesArtifactRetention(
            claim_name="release-data",
            volume_subdirectory="artifacts",
            uri_root="/data/artifacts",
        ),
    )
    context = GoblinContext(
        run_id="run-artifact",
        artifact_root=".goblin-king/artifacts/run-artifact",
        metadata={"job_id": "job-artifact", "project_id": "project-1"},
    )
    policy = ResourcePolicy.model_validate(
        {"filesystem": {"artifact_max_files": 2, "artifact_max_bytes": 4096}}
    )

    manifest = runtime._job_manifest(
        name="gk-example-artifact-run-artifact",
        config_name="gk-example-artifact-run-artifact-input",
        image="artifact:local",
        context=context,
        worker_id="k8s-worker-run-artifact",
        timeout_seconds=30,
        resource_policy=policy,
    )

    pod_spec = manifest["spec"]["template"]["spec"]
    worker, forwarder = pod_spec["containers"]
    assert "retained-artifacts" not in {
        volume_mount["name"] for volume_mount in worker["volumeMounts"]
    }
    assert {
        "name": "retained-artifacts",
        "mountPath": "/goblin-artifact-volume",
    } in forwarder["volumeMounts"]
    assert {
        "name": "retained-artifacts",
        "persistentVolumeClaim": {"claimName": "release-data"},
    } in pod_spec["volumes"]
    assert {
        "name": "GOBLIN_ARTIFACT_DESTINATION_ROOT",
        "value": "/goblin-artifact-volume/artifacts",
    } in forwarder["env"]
    assert {
        "name": "GOBLIN_KING_K8S_ARTIFACT_URI_ROOT",
        "value": "/data/artifacts",
    } in forwarder["env"]
    assert {"name": "GOBLIN_ARTIFACT_PROJECT_ID", "value": "project-1"} in forwarder["env"]
    assert {"name": "GOBLIN_ARTIFACT_MAX_FILES", "value": "2"} in forwarder["env"]
    assert {"name": "GOBLIN_ARTIFACT_MAX_BYTES", "value": "4096"} in forwarder["env"]


def test_kubernetes_observed_run_captures_bounded_logs_before_cleanup(monkeypatch) -> None:
    """Verify validation callers can retain Job diagnostics after transient cleanup."""
    created: dict[str, object] = {}
    deleted: list[str] = []
    pod = SimpleNamespace(
        metadata=SimpleNamespace(name="validation-pod"),
        status=SimpleNamespace(
            container_statuses=[
                SimpleNamespace(
                    name="worker",
                    state=SimpleNamespace(terminated=SimpleNamespace(exit_code=0)),
                )
            ]
        ),
    )

    class FakeBatch:
        def create_namespaced_job(self, *, namespace, body):
            created["namespace"] = namespace
            created["job"] = body

        def read_namespaced_job(self, *, name, namespace):
            return SimpleNamespace(status=SimpleNamespace(succeeded=1, failed=0))

        def delete_namespaced_job(self, *, name, namespace, propagation_policy):
            deleted.append(name)

    class FakeCore:
        def create_namespaced_config_map(self, *, namespace, body):
            created["config"] = body

        def list_namespaced_pod(self, *, namespace, label_selector):
            return SimpleNamespace(items=[pod])

        def read_namespaced_pod_log(self, *, name, namespace, container, **kwargs):
            assert kwargs.get("limit_bytes") == DEFAULT_KUBERNETES_LOG_CAPTURE_BYTES
            return f"{container} log"

        def delete_namespaced_config_map(self, *, name, namespace):
            deleted.append(name)

    runtime = KubernetesRuntime(
        workers=WorkerImageMap.from_definitions(
            {
                "example.validation": WorkerImageDefinition(
                    context=".", image="validation:local"
                )
            }
        ),
        namespace="proof",
        poll_interval_seconds=0,
    )
    monkeypatch.setattr(
        kubernetes_runtime_module,
        "kubernetes_clients",
        lambda: (FakeBatch(), FakeCore()),
    )
    monkeypatch.setattr(
        runtime,
        "_load_result_observed",
        lambda _run_id: KubernetesRunObservation(
            result=GoblinResult.ok(data={"validated": True}),
            result_received=True,
            result_envelope_valid=True,
        ),
    )
    context = GoblinContext(
        run_id="validation-run",
        artifact_root=".goblin-king/artifacts/validation-run",
        metadata={"job_id": "validation-job"},
    )

    observation = runtime.run_observed(
        GoblinDefinition(
            kind="example.validation",
            display_name="Validation",
            module="container.only",
        ),
        None,
        {"value": 1},
        context,
        timeout_seconds=17,
    )

    assert observation.result.status == "success"
    assert observation.job_created is True
    assert observation.result_envelope_valid is True
    assert observation.exit_code == 0
    assert observation.logs == {
        "worker": "worker log",
        "result-forwarder": "result-forwarder log",
    }
    assert created["namespace"] == "proof"
    assert created["job"]["spec"]["activeDeadlineSeconds"] == 17  # type: ignore[index]
    assert len(deleted) == 2


def test_kubernetes_observed_run_captures_logs_when_wait_fails(monkeypatch) -> None:
    """Verify a post-creation API error still captures diagnostics before cleanup."""
    pod = SimpleNamespace(
        metadata=SimpleNamespace(name="failed-validation-pod"),
        status=SimpleNamespace(container_statuses=[]),
    )
    batch = SimpleNamespace(
        create_namespaced_job=lambda **_kwargs: None,
        delete_namespaced_job=lambda **_kwargs: None,
    )
    core = SimpleNamespace(
        create_namespaced_config_map=lambda **_kwargs: None,
        delete_namespaced_config_map=lambda **_kwargs: None,
        list_namespaced_pod=lambda **_kwargs: SimpleNamespace(items=[pod]),
        read_namespaced_pod_log=lambda *, container, **_kwargs: f"{container} failure log",
    )
    runtime = KubernetesRuntime(
        workers=WorkerImageMap.from_definitions(
            {
                "example.validation": WorkerImageDefinition(
                    context=".", image="validation:local"
                )
            }
        ),
        namespace="proof",
    )
    monkeypatch.setattr(
        kubernetes_runtime_module,
        "kubernetes_clients",
        lambda: (batch, core),
    )

    def fail_wait(**_kwargs):
        raise RuntimeError("job status unavailable")

    monkeypatch.setattr(runtime, "_wait_for_result_observed", fail_wait)

    observation = runtime.run_observed(
        GoblinDefinition(
            kind="example.validation",
            display_name="Validation",
            module="container.only",
        ),
        None,
        {},
        GoblinContext(
            run_id="failed-validation-run",
            artifact_root=".goblin-king/artifacts/failed-validation-run",
            metadata={"job_id": "failed-validation-job"},
        ),
        timeout_seconds=17,
    )

    assert observation.result.status == "failed"
    assert observation.job_created is True
    assert "job status unavailable" in (observation.result.error or "")
    assert observation.logs == {
        "worker": "worker failure log",
        "result-forwarder": "result-forwarder failure log",
    }


def test_kubernetes_job_omits_placement_fields_without_metadata() -> None:
    """Verify default Kubernetes manifests do not include placement fields."""
    workers = WorkerImageMap(
        {"example.hello": WorkerImageDefinition(context=".", image="hello:local")},
        root=".",
    )
    runtime = KubernetesRuntime(workers=workers, redis_url="redis://redis:6379/0")
    context = GoblinContext(
        run_id="run-no-placement",
        artifact_root=".goblin-king/artifacts/run-no-placement",
        metadata={"job_id": "job-no-placement"},
    )

    manifest = runtime._job_manifest(
        name="gk-example-hello-run-no-placement",
        config_name="gk-example-hello-run-no-placement-input",
        image="hello:local",
        context=context,
        worker_id="k8s-worker-run-no-placement",
        timeout_seconds=30,
    )

    pod_spec = manifest["spec"]["template"]["spec"]
    assert "nodeSelector" not in pod_spec
    assert "affinity" not in pod_spec
    assert "tolerations" not in pod_spec


def test_kubernetes_job_maps_placement_to_node_selector_and_affinity() -> None:
    """Verify Kubernetes manifests map placement metadata to scheduling fields."""
    workers = WorkerImageMap(
        {"example.placement": WorkerImageDefinition(context=".", image="placement:local")},
        root=".",
    )
    runtime = KubernetesRuntime(workers=workers, redis_url="redis://redis:6379/0")
    context = GoblinContext(
        run_id="run-placement",
        artifact_root=".goblin-king/artifacts/run-placement",
        metadata={
            "job_id": "job-placement",
            "goblin_definition": {
                "kind": "example.placement",
                "metadata": {
                    "placement": {
                        "required": {"node.example.com/pool": "batch"},
                        "preferred": {
                            "node.example.com/zone": "west-a",
                            "node.example.com/disk": "ssd",
                        },
                        "tolerations": [{"operator": "Exists"}],
                    }
                },
            },
        },
    )

    manifest = runtime._job_manifest(
        name="gk-example-placement-run-placement",
        config_name="gk-example-placement-run-placement-input",
        image="placement:local",
        context=context,
        worker_id="k8s-worker-run-placement",
        timeout_seconds=30,
    )

    pod_spec = manifest["spec"]["template"]["spec"]
    assert pod_spec["nodeSelector"] == {"node.example.com/pool": "batch"}
    assert pod_spec["affinity"] == {
        "nodeAffinity": {
            "preferredDuringSchedulingIgnoredDuringExecution": [
                {
                    "weight": 50,
                    "preference": {
                        "matchExpressions": [
                            {
                                "key": "node.example.com/disk",
                                "operator": "In",
                                "values": ["ssd"],
                            },
                            {
                                "key": "node.example.com/zone",
                                "operator": "In",
                                "values": ["west-a"],
                            },
                        ]
                    },
                }
            ]
        }
    }
    assert "tolerations" not in pod_spec


def test_docker_command_includes_resource_policy_flags(tmp_path) -> None:
    """Verify Docker runtime maps supported resource policy fields to docker run flags."""
    runtime = DockerRuntime(
        workers=WorkerImageMap(
            {"example.hello": WorkerImageDefinition(context=".", image="hello:local")},
            root=".",
        )
    )
    context = GoblinContext(
        run_id="run-policy",
        artifact_root=str(tmp_path / "artifacts"),
        metadata={"job_id": "job-policy"},
    )
    policy = ResourcePolicy.model_validate(
        {
            "cpu": {"limit": "500m"},
            "memory": {"limit": "256Mi"},
            "process": {"pids_limit": 64},
            "network": {"mode": "none"},
            "filesystem": {"read_only_root": True, "tmpfs": ["/tmp:size=16m"]},
            "logs": {"max_bytes": 2048},
        }
    )

    command = runtime._docker_run_command(
        image="hello:local",
        run_dir=tmp_path / "run",
        context=context,
        worker_id="worker-policy",
        timeout_seconds=30,
        resource_policy=policy,
    )

    assert ["--name", "worker-policy"] == command[
        command.index("--name") : command.index("--name") + 2
    ]
    assert (
        f"GOBLIN_CONTRACT_VERSION={GOBLIN_CONTAINER_CONTRACT_VERSION}" in command
    )
    assert ["--cpus", "0.5"] == command[command.index("--cpus") : command.index("--cpus") + 2]
    assert ["--memory", "256m"] == command[
        command.index("--memory") : command.index("--memory") + 2
    ]
    assert (
        'GOBLIN_EFFECTIVE_RESOURCE_POLICY_JSON={"cpu":{"limit":"500m"},'
        '"filesystem":{"read_only_root":true,"tmpfs":["/tmp:size=16m"]},'
        '"logs":{"max_bytes":2048},"memory":{"limit":"256Mi"},'
        '"network":{"mode":"none"},"process":{"pids_limit":64}}'
        in command
    )
    assert ["--pids-limit", "64"] == command[
        command.index("--pids-limit") : command.index("--pids-limit") + 2
    ]
    assert ["--network", "none"] == command[
        command.index("--network") : command.index("--network") + 2
    ]
    assert "--read-only" in command
    assert ["--tmpfs", "/tmp:size=16m"] == command[
        command.index("--tmpfs") : command.index("--tmpfs") + 2
    ]
    assert ["--log-opt", "max-size=2048"] == command[
        command.index("--log-opt") : command.index("--log-opt") + 2
    ]


def test_worker_env_metadata_is_normalized() -> None:
    """Verify project metadata becomes deterministic worker environment config."""
    definition = GoblinDefinition(
        kind="example.env",
        display_name="Env",
        module="container.only",
        metadata={
            "env": {"MODE": "demo", "COUNT": 2, "SKIP": None},
            "secret_refs": ["DEMO_SECRET", ""],
        },
    )

    assert _worker_env(definition) == {"COUNT": "2", "MODE": "demo"}
    assert _worker_secret_refs(definition) == ["DEMO_SECRET"]


def test_docker_command_includes_project_env_and_secret_refs(tmp_path, monkeypatch) -> None:
    """Verify Docker workers receive env while secret values stay out of argv."""
    monkeypatch.setenv("DEMO_SECRET", "secret-value")
    runtime = DockerRuntime(
        workers=WorkerImageMap(
            {"example.hello": WorkerImageDefinition(context=".", image="hello:local")},
            root=".",
        )
    )
    context = GoblinContext(
        run_id="run-env",
        artifact_root=str(tmp_path / "artifacts"),
        metadata={"job_id": "job-env"},
    )

    command = runtime._docker_run_command(
        image="hello:local",
        run_dir=tmp_path / "run",
        context=context,
        worker_id="worker-env",
        timeout_seconds=30,
        worker_env={"MODE": "demo"},
        secret_refs=["DEMO_SECRET", "MISSING_SECRET"],
    )

    assert "MODE=demo" in command
    assert "DEMO_SECRET" in command
    assert "secret-value" not in command
    assert "MISSING_SECRET" not in command


def test_docker_runtime_emits_bounded_container_logs(tmp_path, monkeypatch) -> None:
    """Verify Docker wrapper stdout/stderr are preserved as bounded lifecycle events."""

    class RecordingEventBus:
        def __init__(self) -> None:
            self.events = []

        def emit(self, event_type: str, **payload) -> None:
            self.events.append({"event_type": event_type, **payload})

    event_bus = RecordingEventBus()
    runtime = DockerRuntime(
        workers=WorkerImageMap(
            {"example.hello": WorkerImageDefinition(context=".", image="hello:local")},
            root=".",
        ),
        run_root=tmp_path / "runs",
        event_bus=event_bus,
    )
    context = GoblinContext(
        run_id="run-logs",
        artifact_root=str(tmp_path / "artifacts" / "run-logs"),
        metadata={"job_id": "job-logs"},
    )
    definition = GoblinDefinition(
        kind="example.hello",
        display_name="Hello",
        module="unused.by.docker",
    )
    policy = ResourcePolicy.model_validate({"logs": {"max_bytes": 10}})

    def fake_run(command, **_kwargs):
        assert ["--name", "worker-run-logs"] == command[
            command.index("--name") : command.index("--name") + 2
        ]
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="abcdefghi",
            stderr="1234567",
        )

    monkeypatch.setattr("goblin_king.runtime.subprocess.run", fake_run)
    monkeypatch.setattr(runtime, "_record_worker_heartbeats", lambda _context: None)
    monkeypatch.setattr(
        runtime,
        "_load_result",
        lambda _run_id, _result_path: GoblinResult.ok(data={"ok": True}),
    )

    result = runtime.run(definition, None, {}, context, resource_policy=policy)

    assert result.status == "success"
    log_event = next(
        event for event in event_bus.events if event["event_type"] == "worker.container_logs"
    )
    assert log_event["worker_id"] == "worker-run-logs"
    assert log_event["payload"] == {
        "kind": "example.hello",
        "image": "hello:local",
        "container_name": "worker-run-logs",
        "exit_code": 0,
        "timed_out": False,
        "stdout": "efghi",
        "stderr": "34567",
        "stdout_truncated": True,
        "stderr_truncated": True,
        "truncated": True,
        "max_bytes": 10,
        "stdout_bytes": 9,
        "stderr_bytes": 7,
    }


def test_kubernetes_job_includes_resource_policy_fields() -> None:
    """Verify Kubernetes runtime maps resource policy into container resources."""
    workers = WorkerImageMap(
        {"example.hello": WorkerImageDefinition(context=".", image="hello:local")},
        root=".",
    )
    runtime = KubernetesRuntime(workers=workers, redis_url="redis://redis:6379/0")
    context = GoblinContext(
        run_id="run-policy",
        artifact_root=".goblin-king/artifacts/run-policy",
        metadata={"job_id": "job-policy"},
    )
    policy = ResourcePolicy.model_validate(
        {
            "cpu": {"request": "100m", "limit": "1"},
            "memory": {"request": "64Mi", "limit": "512Mi"},
            "filesystem": {"read_only_root": True},
        }
    )

    manifest = runtime._job_manifest(
        name="gk-example-hello-run-policy",
        config_name="gk-example-hello-run-policy-input",
        image="hello:local",
        context=context,
        worker_id="k8s-worker-run-policy",
        timeout_seconds=30,
        resource_policy=policy,
    )

    worker = manifest["spec"]["template"]["spec"]["containers"][0]
    assert worker["resources"] == {
        "requests": {"cpu": "100m", "memory": "64Mi"},
        "limits": {"cpu": "1", "memory": "512Mi"},
    }
    assert worker["securityContext"] == {"readOnlyRootFilesystem": True}
    assert {
        "name": "GOBLIN_EFFECTIVE_RESOURCE_POLICY_JSON",
        "value": (
            '{"cpu":{"limit":"1","request":"100m"},'
            '"filesystem":{"read_only_root":true},'
            '"memory":{"limit":"512Mi","request":"64Mi"}}'
        ),
    } in worker["env"]
