import json
from pathlib import Path
from types import SimpleNamespace

from goblin_king.contracts import GoblinDefinition
from goblin_king.registry import GoblinRegistry
from goblin_king.validation import validate_workers
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
