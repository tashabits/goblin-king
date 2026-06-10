"""Local runtime tests for in-process goblin execution."""

from goblin_king.contracts import GoblinContext
from goblin_king.registry import GoblinRegistry
from goblin_king.resource_policies import ResourcePolicy
from goblin_king.runtime import DockerRuntime, InProcessRuntime, KubernetesRuntime
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

    forwarder = next(
        container for container in containers if container["name"] == "result-forwarder"
    )
    assert forwarder["image"] == "goblin-king:test"
    assert forwarder["command"][0:2] == ["python", "-c"]
    assert {"name": "GOBLIN_REDIS_URL", "value": "redis://redis:6379/0"} in forwarder["env"]
    assert {"name": "GOBLIN_RESULT_PATH", "value": "/goblin-result/result.json"} in forwarder[
        "env"
    ]
    assert {"name": "result", "mountPath": "/goblin-result"} in forwarder["volumeMounts"]


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

    assert ["--cpus", "0.5"] == command[command.index("--cpus") : command.index("--cpus") + 2]
    assert ["--memory", "256m"] == command[
        command.index("--memory") : command.index("--memory") + 2
    ]
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
