from __future__ import annotations

import json
import zipfile
from io import BytesIO

import pytest

from goblin_king.repository_bundles import (
    RepositoryBundleError,
    RepositoryBundleLimits,
    parse_repository_bundle,
)


def _bundle(files: dict[str, bytes | str]) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, content in files.items():
            data = content.encode("utf-8") if isinstance(content, str) else content
            archive.writestr(path, data)
    return buffer.getvalue()


def _manifest(**overrides: object) -> str:
    payload = {
        "schema_version": 1,
        "name": "shared.hello",
        "type": "notebook_function",
        "entrypoint": "hello.py",
        "display_name": "Shared Hello",
        "description": "A small shared function.",
        "tags": ["demo", "Demo"],
        "function_name": "run",
    }
    payload.update(overrides)
    return json.dumps(payload)


def test_function_bundle_preview_builds_repository_submit_payload() -> None:
    preview = parse_repository_bundle(
        _bundle(
            {
                "goblin-repository.json": _manifest(),
                "hello.py": (
                    "def run(payload):\n"
                    "    return {'hello': payload.get('name', 'world')}\n"
                ),
                "notes.md": "# ignored in v1\n",
            }
        )
    )

    assert preview.manifest.name == "shared.hello"
    assert preview.submit_payload["type"] == "notebook_function"
    assert preview.submit_payload["function_name"] == "run"
    assert preview.submit_payload["source"].startswith("def run")
    assert preview.submit_payload["metadata"]["bundle_entrypoint"] == "hello.py"
    assert preview.submit_payload["tags"] == ["demo"]
    assert preview.warnings == [
        "extra files are shown for review but not executed in bundle schema v1"
    ]


def test_service_bundle_combines_inline_and_file_requirements() -> None:
    preview = parse_repository_bundle(
        _bundle(
            {
                "goblin-repository.json": _manifest(
                    name="shared.long-hello",
                    type="notebook_service",
                    entrypoint="service/app.py",
                    app_name="app",
                    requirements=["fastapi>=0.115,<1"],
                    requirements_file="requirements.txt",
                    probe_path="/hello",
                ),
                "service/app.py": "from fastapi import FastAPI\napp = FastAPI()\n",
                "requirements.txt": "# comment\nuvicorn>=0.30,<1\nfastapi>=0.115,<1\n",
            }
        )
    )

    assert preview.submit_payload["type"] == "notebook_service"
    assert preview.submit_payload["app_name"] == "app"
    assert preview.submit_payload["requirements"] == [
        "fastapi>=0.115,<1",
        "uvicorn>=0.30,<1",
    ]
    assert preview.submit_payload["probe_path"] == "/hello"


@pytest.mark.parametrize(
    ("path", "message"),
    [
        ("../hello.py", "bundle path is not safe"),
        ("/hello.py", "bundle paths must be relative"),
        ("src/../hello.py", "bundle path is not safe"),
    ],
)
def test_bundle_rejects_unsafe_member_paths(path: str, message: str) -> None:
    with pytest.raises((RepositoryBundleError, ValueError), match=message):
        parse_repository_bundle(
            _bundle(
                {
                    "goblin-repository.json": _manifest(entrypoint=path),
                    path: "def run(payload): return payload",
                }
            )
        )


def test_bundle_rejects_missing_manifest() -> None:
    with pytest.raises(RepositoryBundleError, match="goblin-repository.json"):
        parse_repository_bundle(_bundle({"hello.py": "def run(payload): return payload"}))


def test_bundle_rejects_missing_entrypoint() -> None:
    with pytest.raises(RepositoryBundleError, match="entrypoint is missing"):
        parse_repository_bundle(_bundle({"goblin-repository.json": _manifest()}))


def test_bundle_rejects_binary_entrypoint() -> None:
    with pytest.raises(RepositoryBundleError, match="entrypoint must be UTF-8 text"):
        parse_repository_bundle(
            _bundle(
                {
                    "goblin-repository.json": _manifest(),
                    "hello.py": b"\xff\xfe\x00",
                }
            )
        )


def test_bundle_rejects_oversized_content() -> None:
    data = _bundle(
        {
            "goblin-repository.json": _manifest(),
            "hello.py": "def run(payload): return payload",
        }
    )

    with pytest.raises(RepositoryBundleError, match="bundle is too large"):
        parse_repository_bundle(data, limits=RepositoryBundleLimits(max_bundle_bytes=8))
