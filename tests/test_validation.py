import json
from pathlib import Path
from types import SimpleNamespace

from goblin_king import api as api_module
from goblin_king.contracts import GoblinDefinition, GoblinResult, NotebookGoblinRecord, utc_now
from goblin_king.kubernetes_runtime_settings import KubernetesRuntimeSettings
from goblin_king.notebooks import notebook_validation_identity
from goblin_king.registry import GoblinRegistry
from goblin_king.validation import WorkerValidationResult, _validate_one, validate_workers
from goblin_king.workers import WorkerImageDefinition, WorkerImageMap


def test_validate_workers_reports_unknown_kind() -> None:
    registry = GoblinRegistry.from_path("examples/cross-language-goblins.json")
    workers = WorkerImageMap.from_path("examples/cross-language-images.json")

    results = validate_workers(
        registry=registry,
        workers=workers,
        input_payload={"target": "test"},
        kinds=["example.missing"],
    )

    assert len(results) == 1
    assert results[0].ok is False
    assert results[0].error == "unknown goblin kind: example.missing"


def test_validate_workers_reports_missing_context(tmp_path) -> None:
    registry_path = tmp_path / "goblins.json"
    images_path = tmp_path / "images.json"
    registry_path.write_text(
        json.dumps(
            {
                "goblins": [
                    {
                        "kind": "example.missing-context",
                        "display_name": "Missing Context",
                        "module": "examples.goblins.container_only",
                        "entrypoint": "run",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    images_path.write_text(
        json.dumps(
            {
                "workers": {
                    "example.missing-context": {
                        "context": "missing",
                        "dockerfile": "Dockerfile",
                        "image": "missing:local",
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    results = validate_workers(
        registry=GoblinRegistry.from_path(registry_path),
        workers=WorkerImageMap.from_path(images_path),
        input_payload={},
    )

    assert results[0].ok is False
    assert "worker context missing" in (results[0].error or "")


def test_validate_workers_reports_missing_prebuilt_image(monkeypatch) -> None:
    def fake_run(*_args, **_kwargs):
        return SimpleNamespace(returncode=1, stderr="No such image", stdout="")

    monkeypatch.setattr("goblin_king.validation.subprocess.run", fake_run)
    registry = GoblinRegistry.from_definitions(
        [
            GoblinDefinition(
                kind="example.prebuilt",
                display_name="Example Prebuilt",
                module="goblin_king.container_only",
            )
        ]
    )
    workers = WorkerImageMap.from_definitions(
        {
            "example.prebuilt": WorkerImageDefinition(
                context=Path("."),
                image="missing-prebuilt:local",
            )
        }
    )

    results = validate_workers(
        registry=registry,
        workers=workers,
        input_payload={},
        prebuilt_image=True,
    )

    assert results[0].ok is False
    assert "worker image unavailable: missing-prebuilt:local" in (results[0].error or "")


def test_validate_workers_uses_shared_run_root_for_docker_volume(monkeypatch) -> None:
    captured = {}

    class FakeDockerRuntime:
        def __init__(self, *, workers, redis_url, run_root):
            captured["run_root"] = Path(run_root)

    def fake_validate_one(**kwargs):
        return WorkerValidationResult(kind=kwargs["kind"], ok=True)

    monkeypatch.setenv("GOBLIN_KING_DOCKER_DATA_VOLUME", "goblin-king-data")
    monkeypatch.setenv("GOBLIN_KING_RUN_ROOT", "/data/goblin-runs")
    monkeypatch.setattr("goblin_king.validation.DockerRuntime", FakeDockerRuntime)
    monkeypatch.setattr("goblin_king.validation._validate_one", fake_validate_one)
    registry = GoblinRegistry.from_definitions(
        [
            GoblinDefinition(
                kind="example.prebuilt",
                display_name="Example Prebuilt",
                module="goblin_king.container_only",
            )
        ]
    )
    workers = WorkerImageMap.from_definitions(
        {
            "example.prebuilt": WorkerImageDefinition(
                context=Path("."),
                image="prebuilt:local",
            )
        }
    )

    results = validate_workers(
        registry=registry,
        workers=workers,
        input_payload={},
        prebuilt_image=True,
    )

    assert results[0].ok is True
    assert captured["run_root"] == Path("/data/goblin-runs")


def test_validate_workers_uses_home_temp_root_on_macos(monkeypatch) -> None:
    captured = {}

    class FakeDockerRuntime:
        def __init__(self, *, workers, redis_url, run_root):
            captured["run_root"] = Path(run_root)

    def fake_validate_one(**kwargs):
        return WorkerValidationResult(kind=kwargs["kind"], ok=True)

    monkeypatch.setattr("goblin_king.validation.sys.platform", "darwin")
    monkeypatch.delenv("GOBLIN_KING_DOCKER_DATA_VOLUME", raising=False)
    monkeypatch.setattr("goblin_king.validation.DockerRuntime", FakeDockerRuntime)
    monkeypatch.setattr("goblin_king.validation._validate_one", fake_validate_one)
    registry = GoblinRegistry.from_definitions(
        [
            GoblinDefinition(
                kind="example.prebuilt",
                display_name="Example Prebuilt",
                module="goblin_king.container_only",
            )
        ]
    )
    workers = WorkerImageMap.from_definitions(
        {
            "example.prebuilt": WorkerImageDefinition(
                context=Path("."),
                image="prebuilt:local",
            )
        }
    )

    results = validate_workers(
        registry=registry,
        workers=workers,
        input_payload={},
        prebuilt_image=True,
    )

    assert results[0].ok is True
    assert captured["run_root"].name == "runs"
    assert captured["run_root"].parent.parent == Path.home()


def test_validate_one_uses_short_label_safe_job_id_for_long_kinds(
    tmp_path,
    monkeypatch,
) -> None:
    captured = {}
    kind = (
        "repository.1bb95a63-e66f-4a04-9eb2-3bbcc57ec4e7."
        "k8s.repository.hello.1781754363.v1"
    )

    class FakeRuntime:
        def __init__(self) -> None:
            self.run_root = tmp_path / "runs"
            self.docker_executable = "docker"

        def run(self, *, context, **_kwargs):
            captured["job_id"] = context.metadata["job_id"]
            captured["kind"] = context.metadata["kind"]
            result_dir = self.run_root / context.run_id
            result_dir.mkdir(parents=True)
            result = GoblinResult.ok(data={"ok": True})
            (result_dir / "result.json").write_text(
                result.model_dump_json(),
                encoding="utf-8",
            )
            return result

    monkeypatch.setattr(
        "goblin_king.validation.inspect_image_identity",
        lambda _docker_executable, image: (f"sha256:{image}", None),
    )

    result = _validate_one(
        kind=kind,
        workers=WorkerImageMap.from_definitions(
            {kind: WorkerImageDefinition(context=Path("."), image="worker:local")}
        ),
        runtime=FakeRuntime(),
        input_payload={},
        build=False,
        require_success=True,
        prebuilt_image=True,
        timeout_seconds=30,
    )

    assert result.ok is True
    assert captured["kind"] == kind
    assert len(captured["job_id"]) <= 63
    assert captured["job_id"].startswith("validation-repository")


def test_api_kubernetes_notebook_validation_uses_short_job_id(monkeypatch) -> None:
    captured = {}
    kind = (
        "repository.e9f3043b-e1ed-40f5-b2d1-a6edc27f8541."
        "k8s.repository.hello.1781754711.v1"
    )
    record = NotebookGoblinRecord(
        kind=kind,
        display_name="Repository Hello",
        image="goblin-king-notebook-python-function:local",
        source="def run(payload):\n    return {'ok': True}\n",
        source_hash="sha256-source",
        function_name="run",
        created_at=utc_now(),
        updated_at=utc_now(),
    )

    class FakeKubernetesRuntime:
        def run(self, _definition, _entrypoint, _payload, context, **_kwargs):
            captured["job_id"] = context.metadata["job_id"]
            captured["kind"] = context.metadata["kind"]
            captured["run_id"] = context.run_id
            return GoblinResult.ok(data={"ok": True})

    def fake_build_kubernetes_runtime(**kwargs):
        captured["settings"] = kwargs["settings"]
        return FakeKubernetesRuntime()

    def fake_finalize(validation, cleanup_settings, context):
        captured["cleanup_settings"] = cleanup_settings
        captured["cleanup_run_id"] = context.run_id
        return validation

    monkeypatch.setattr(
        api_module,
        "build_kubernetes_runtime",
        fake_build_kubernetes_runtime,
    )
    monkeypatch.setattr(
        api_module,
        "with_kubernetes_validation_cleanup",
        fake_finalize,
    )

    settings = KubernetesRuntimeSettings(
        result_forwarder_image="registry.example/control@sha256:" + "a" * 64,
        workload_security_profile="restricted-v1",
    )
    result = api_module._validate_notebook_with_kubernetes(
        record=record,
        input_payload={},
        require_success=True,
        timeout_seconds=30,
        redis_url="redis://redis:6379/0",
        event_bus=None,
        kubernetes_runtime_settings=settings,
    )

    assert result.ok is True
    assert captured["kind"] == kind
    assert captured["settings"] is settings
    assert captured["cleanup_settings"] is settings
    assert captured["cleanup_run_id"] == captured["run_id"]
    assert result.image_digest == notebook_validation_identity(
        settings.validation_image_identity(record.image, record.kind),
        record.source_hash,
    )
    assert len(captured["job_id"]) <= 63
    assert captured["job_id"].startswith("validation-repository")
