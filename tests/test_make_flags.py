from __future__ import annotations

import shutil
import subprocess

import pytest


def _make_helm_dry_run(*variables: str) -> str:
    if shutil.which("make") is None:
        pytest.skip("make is not available")
    completed = subprocess.run(
        [
            "make",
            "-n",
            "helm-up",
            "HELM_WITH_JUPYTERHUB=1",
            "JUPYTERHUB_STACK_IMAGE_TAG=test",
            *variables,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout


def test_directory_stack_flags_enable_directory_api_and_ui() -> None:
    output = _make_helm_dry_run(
        "GOBLIN_DIRECTORY_ENABLED=1",
        "GOBLIN_DIRECTORY_UI_ENABLED=1",
    )

    assert "--set repository.enabled=true" in output
    assert "--set directoryUi.enabled=true" in output
    assert "--set singleuser.image.name=goblin-king-directory-singleuser" in output


def test_legacy_repository_stack_flags_still_enable_directory_api_and_ui() -> None:
    output = _make_helm_dry_run(
        "GOBLIN_REPOSITORY_ENABLED=1",
        "GOBLIN_REPOSITORY_UI_ENABLED=1",
    )

    assert "--set repository.enabled=true" in output
    assert "--set directoryUi.enabled=true" in output
    assert "--set singleuser.image.name=goblin-king-directory-singleuser" in output


def test_directory_ui_flag_does_not_enable_directory_api() -> None:
    output = _make_helm_dry_run("GOBLIN_DIRECTORY_UI_ENABLED=1")

    assert "--set repository.enabled=true" not in output
    assert "--set directoryUi.enabled=true" in output
