"""Template generation for reusable goblin packages."""

from __future__ import annotations

import json
import re
from pathlib import Path

from goblin_king.contracts import GOBLIN_KIND_PATTERN


class TemplateError(ValueError):
    """Raised when a package template cannot be generated safely."""


def init_package(target_dir: str | Path, *, kind: str, package_name: str, image: str) -> Path:
    """Create a reusable goblin package skeleton with a Docker worker folder."""
    if not GOBLIN_KIND_PATTERN.match(kind):
        raise TemplateError("kind must use lowercase letters, digits, dots, or dashes")
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", package_name):
        raise TemplateError("package_name must be a valid Python package identifier")
    root = Path(target_dir)
    if root.exists() and any(root.iterdir()):
        raise TemplateError(f"target directory is not empty: {root}")
    root.mkdir(parents=True, exist_ok=True)

    package_dir = root / package_name
    tests_dir = root / "tests"
    worker_dir = root / "workers" / kind
    package_dir.mkdir(parents=True)
    tests_dir.mkdir()
    worker_dir.mkdir(parents=True)

    module_name = f"{package_name}.goblin"
    _write(root / "pyproject.toml", _pyproject(package_name, kind))
    _write(package_dir / "__init__.py", '"""Generated goblin package."""\n')
    _write(package_dir / "goblin.py", _goblin_module(kind))
    _write(tests_dir / "test_goblin.py", _test_module(package_name, kind))
    _write(root / "goblins.json", _registry_json(kind, module_name))
    _write(root / "goblin-images.json", _images_json(kind, image))
    _write(worker_dir / "Dockerfile", _worker_dockerfile())
    _write(worker_dir / ".dockerignore", "__pycache__/\n*.pyc\n.pytest_cache/\n")
    _write(worker_dir / "worker.py", _worker_module())
    _write(root / "README.md", _readme(kind, package_name, image))
    return root


def _write(path: Path, content: str) -> None:
    """Write a generated text file with UTF-8 encoding."""
    path.write_text(content, encoding="utf-8")


def _pyproject(package_name: str, kind: str) -> str:
    """Return pyproject metadata with a goblin entry point."""
    return f"""[build-system]
requires = ["hatchling>=1.24"]
build-backend = "hatchling.build"

[project]
name = "{package_name.replace('_', '-')}"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["goblin-king"]

[project.entry-points."goblin_king.goblins"]
{kind.replace('.', '_').replace('-', '_')} = "{package_name}.goblin:definition"

[tool.hatch.build.targets.wheel]
packages = ["{package_name}"]
"""


def _goblin_module(kind: str) -> str:
    """Return generated Python goblin module content."""
    display = kind.replace(".", " ").replace("-", " ").title()
    return f'''"""Generated goblin definition and in-process entrypoint."""

from __future__ import annotations

from goblin_king import GoblinContext, GoblinDefinition, GoblinResult

GOBLIN_KIND = "{kind}"


def definition() -> GoblinDefinition:
    """Return the Goblin King package entry point definition."""
    return GoblinDefinition(
        kind=GOBLIN_KIND,
        display_name="{display}",
        module=__name__,
        entrypoint="run",
        timeout_seconds=30,
        max_retries=0,
    )


def run(input_payload: dict, ctx: GoblinContext) -> GoblinResult:
    """Echo input and run metadata for local in-process debugging."""
    return GoblinResult.ok(
        data={{
            "message": input_payload.get("message", ""),
            "echoed": input_payload,
            "run_id": ctx.run_id,
        }}
    )
'''


def _test_module(package_name: str, kind: str) -> str:
    """Return generated package test content."""
    return f'''"""Tests for the generated {kind} goblin."""

from {package_name}.goblin import GOBLIN_KIND, definition


def test_definition_kind() -> None:
    """Verify the package entry point definition uses the expected kind."""
    assert GOBLIN_KIND == "{kind}"
    assert definition().kind == "{kind}"
'''


def _registry_json(kind: str, module_name: str) -> str:
    """Return generated registry stub JSON."""
    return json.dumps(
        {
            "goblins": [
                {
                    "kind": kind,
                    "display_name": kind,
                    "module": module_name,
                    "entrypoint": "run",
                    "timeout_seconds": 30,
                    "max_retries": 0,
                }
            ]
        },
        indent=2,
    ) + "\n"


def _images_json(kind: str, image: str) -> str:
    """Return generated worker image map JSON."""
    return json.dumps(
        {
            "workers": {
                kind: {
                    "context": f"workers/{kind}",
                    "dockerfile": "Dockerfile",
                    "image": image,
                }
            }
        },
        indent=2,
    ) + "\n"


def _worker_dockerfile() -> str:
    """Return a minimal Python Docker worker file."""
    return """FROM python:3.12-slim

WORKDIR /worker
COPY worker.py /worker/worker.py
RUN pip install --no-cache-dir "redis>=5,<7"

ENTRYPOINT ["python", "/worker/worker.py"]
"""


def _worker_module() -> str:
    """Return generated Docker worker code."""
    return '''"""Generated container worker implementing the Goblin King worker contract."""

from __future__ import annotations

import json
import os
from pathlib import Path

from redis import Redis


def main() -> None:
    """Read worker inputs, publish a result to Redis, and write fallback output."""
    input_payload = json.loads(Path(os.environ["GOBLIN_INPUT_PATH"]).read_text())
    context = json.loads(Path(os.environ["GOBLIN_CONTEXT_PATH"]).read_text())
    result_path = Path(os.environ["GOBLIN_RESULT_PATH"])
    run_id = os.environ["GOBLIN_RUN_ID"]

    result = {
        "status": "success",
        "data": {
            "message": input_payload.get("message", ""),
            "echoed": input_payload,
            "run_id": context["run_id"],
        },
        "artifacts": [],
        "metrics": {},
        "handoff": [],
        "error": None,
    }
    result_json = json.dumps(result)
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(result_json, encoding="utf-8")
    Redis.from_url(os.environ["GOBLIN_REDIS_URL"]).set(
        f"goblin-king:results:{run_id}",
        result_json,
        ex=3600,
    )


if __name__ == "__main__":
    main()
'''


def _readme(kind: str, package_name: str, image: str) -> str:
    """Return generated package README content."""
    return f"""# {package_name}

Generated Goblin King package for `{kind}`.

- Python entry point group: `goblin_king.goblins`
- Worker image: `{image}`
- Worker folder: `workers/{kind}`
"""
