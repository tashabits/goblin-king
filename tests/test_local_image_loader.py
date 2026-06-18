from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType


def _load_module() -> ModuleType:
    path = (
        Path(__file__).parents[1]
        / "examples"
        / "jupyterhub-goblin-king"
        / "local_image_loader.py"
    )
    spec = importlib.util.spec_from_file_location("jupyterhub_local_image_loader", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_kind_loader_treats_permission_error_as_unavailable(monkeypatch) -> None:
    loader = _load_module()

    def fake_run(args, **kwargs):
        assert args == ["kind", "get", "clusters"]
        raise PermissionError("permission denied")

    monkeypatch.setattr(loader.subprocess, "run", fake_run)

    assert loader._load_kind_images(["goblin-king:test"], "kind") is False


def test_current_context_controls_kind_loading(monkeypatch) -> None:
    loader = _load_module()
    loaded: list[tuple[list[str], str]] = []

    monkeypatch.setattr(loader, "_current_context", lambda: "kind-local")

    def fake_load_kind(images: list[str], kind_cluster: str) -> bool:
        loaded.append((images, kind_cluster))
        return True

    monkeypatch.setattr(loader, "_load_kind_images", fake_load_kind)
    monkeypatch.setattr(
        loader,
        "_load_docker_desktop_images",
        lambda images: (_ for _ in ()).throw(AssertionError("unexpected Docker Desktop load")),
    )

    loader.load_images_for_current_context(["goblin-king:test"], "local")

    assert loaded == [(["goblin-king:test"], "local")]


def test_docker_desktop_context_skips_kind_loading(monkeypatch) -> None:
    loader = _load_module()
    loaded: list[list[str]] = []

    monkeypatch.setattr(loader, "_current_context", lambda: "docker-desktop")
    monkeypatch.setattr(
        loader,
        "_load_kind_images",
        lambda images, kind_cluster: (_ for _ in ()).throw(AssertionError("unexpected kind load")),
    )
    monkeypatch.setattr(loader, "_load_docker_desktop_images", lambda images: loaded.append(images))

    loader.load_images_for_current_context(["goblin-king:test"], "kind")

    assert loaded == [["goblin-king:test"]]
