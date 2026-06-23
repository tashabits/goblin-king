from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
STACK_DIR = ROOT / "examples" / "jupyterhub-goblin-king"


def _load_prepare_module() -> ModuleType:
    sys.path.insert(0, str(STACK_DIR))
    try:
        spec = importlib.util.spec_from_file_location(
            "prepare_stack_images",
            STACK_DIR / "prepare_stack_images.py",
        )
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(STACK_DIR))


def test_stack_image_prepare_can_include_jupyterlab_singleuser_image() -> None:
    module = _load_prepare_module()

    images = module._images("proof-tag", include_singleuser=True)
    contexts = module._contexts(images)

    assert images["singleuser"] == "goblin-king-directory-singleuser:proof-tag"
    assert contexts[images["singleuser"]].context == "."
    assert (
        contexts[images["singleuser"]].dockerfile
        == "examples/jupyterhub-goblin-king/singleuser/Dockerfile"
    )


def test_stack_image_prepare_keeps_singleuser_optional() -> None:
    module = _load_prepare_module()

    images = module._images("proof-tag")
    contexts = module._contexts(images)

    assert "singleuser" not in images
    assert all("singleuser" not in image for image in contexts)


def test_directory_ui_and_picker_proofs_split_singleuser_image() -> None:
    ui_proof = (STACK_DIR / "directory_ui_proof.py").read_text(encoding="utf-8")
    picker_proof = (STACK_DIR / "directory_picker_proof.py").read_text(encoding="utf-8")

    assert '"GOBLIN_DIRECTORY_PICKER_ENABLED=0"' in ui_proof
    assert '"GOBLIN_DIRECTORY_PICKER_ENABLED=1"' in picker_proof
    assert "include_singleuser=True" in picker_proof
