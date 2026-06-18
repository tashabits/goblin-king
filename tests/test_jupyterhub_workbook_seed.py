from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SEED_SCRIPT = ROOT / "examples" / "jupyterhub-goblin-king" / "seed_user_workbooks.py"


def load_seed_module():
    spec = importlib.util.spec_from_file_location("seed_user_workbooks", SEED_SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_default_seed_workbooks_are_stable_examples() -> None:
    module = load_seed_module()

    names = [path.name for path in module.DEFAULT_WORKBOOKS]

    assert names == [
        "workbook-launch.ipynb",
        "workbook-directory-submit.ipynb",
        "workbook-directory-admin.ipynb",
        "workbook-directory-consume.ipynb",
    ]
    assert all((ROOT / path).is_file() for path in module.DEFAULT_WORKBOOKS)


def test_parse_seed_user_requires_user_and_token() -> None:
    module = load_seed_module()

    assert module._parse_user("alice:secret") == ("alice", "secret")

    with pytest.raises(module.argparse.ArgumentTypeError):
        module._parse_user("alice")


def test_copy_workbooks_writes_into_examples_folder(monkeypatch, tmp_path) -> None:
    module = load_seed_module()
    workbook = tmp_path / "proof.ipynb"
    workbook.write_bytes(b'{"cells": []}')
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))

        class Result:
            returncode = 0

        return Result()

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    copied = module._copy_workbooks(
        namespace="default",
        pod="jupyter-alice",
        destination="examples",
        workbooks=[workbook],
    )

    assert copied == ["/home/jovyan/examples/proof.ipynb"]
    assert calls[0][0][-1] == "mkdir -p /home/jovyan/examples"
    assert calls[1][0][-1] == "cat > /home/jovyan/examples/proof.ipynb"
    assert calls[1][1]["input"] == b'{"cells": []}'


def test_seeded_workbook_names_use_directory_wording() -> None:
    module = load_seed_module()

    assert (
        module._seeded_workbook_name("workbook-directory-submit.ipynb")
        == "workbook-directory-submit.ipynb"
    )
    assert module._seeded_workbook_name("workbook-launch.ipynb") == "workbook-launch.ipynb"
