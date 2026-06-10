"""Template generation for reusable goblin packages."""

from __future__ import annotations

import json
import re
from pathlib import Path

from goblin_king.contracts import GOBLIN_KIND_PATTERN
from goblin_king.jsonio import pretty_json_line
from goblin_king.versions import PROJECT_CONFIG_API_VERSION, PROJECT_CONFIG_KIND


class TemplateError(ValueError):
    """Raised when a package template cannot be generated safely."""


def init_package(
    target_dir: str | Path,
    *,
    kind: str,
    package_name: str,
    image: str,
    include_long_service: bool = True,
) -> Path:
    """Create a reusable goblin plugin package with worker folders."""
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
    long_service_kind = f"{kind}.long-service"
    long_service_image = _long_service_image(image)
    worker_dir = root / "workers" / kind
    long_worker_dir = root / "workers" / long_service_kind
    package_dir.mkdir(parents=True)
    tests_dir.mkdir()
    worker_dir.mkdir(parents=True)
    if include_long_service:
        long_worker_dir.mkdir(parents=True)

    module_name = f"{package_name}.goblin"
    service_module_name = f"{package_name}.long_service"
    _write(
        root / "pyproject.toml",
        _pyproject(package_name, kind, long_service_kind if include_long_service else None),
    )
    _write(package_dir / "__init__.py", '"""Generated goblin package."""\n')
    _write(package_dir / "goblin.py", _goblin_module(kind))
    if include_long_service:
        _write(package_dir / "long_service.py", _long_service_module(long_service_kind))
    _write(
        tests_dir / "test_goblin.py",
        _test_module(package_name, kind, long_service_kind, include_long_service),
    )
    _write(
        root / "goblins.json",
        _registry_json(
            kind,
            module_name,
            long_service_kind if include_long_service else None,
            service_module_name,
        ),
    )
    _write(
        root / "goblin-images.json",
        _images_json(
            kind,
            image,
            long_service_kind if include_long_service else None,
            long_service_image,
        ),
    )
    _write(root / "goblin-king-project.json", _project_settings_json())
    _write(root / "goblin-king-api.json", _api_settings_json())
    _write(worker_dir / "Dockerfile", _worker_dockerfile())
    _write(worker_dir / ".dockerignore", "__pycache__/\n*.pyc\n.pytest_cache/\n")
    _write(worker_dir / "worker.py", _worker_module())
    if include_long_service:
        _write(long_worker_dir / "Dockerfile", _service_dockerfile())
        _write(long_worker_dir / ".dockerignore", "__pycache__/\n*.pyc\n.pytest_cache/\n")
        _write(long_worker_dir / "service.py", _service_module(long_service_kind))
    _write(root / "README.md", _readme(kind, package_name, image, include_long_service))
    return root


def init_project(target_dir: str | Path, *, prefix: str = "project") -> Path:
    """Create a standalone container-contract adopter project template."""
    if not re.match(r"^[a-z][a-z0-9-]*$", prefix):
        raise TemplateError("prefix must use lowercase letters, digits, or dashes")
    root = Path(target_dir)
    if root.exists() and any(root.iterdir()):
        raise TemplateError(f"target directory is not empty: {root}")
    root.mkdir(parents=True, exist_ok=True)

    hello_kind = f"{prefix}.hello"
    artifact_kind = f"{prefix}.artifact"
    for directory in [
        root / "workers" / hello_kind,
        root / "workers" / artifact_kind,
        root / "inputs",
        root / "schemas",
    ]:
        directory.mkdir(parents=True, exist_ok=True)

    _write(root / "goblin-king-project.json", _adopter_project_json(prefix))
    _write(root / "goblin-images.json", pretty_json_line({"workers": {}}))
    _write(root / "goblin-king-api.json", _api_settings_json())
    _write(root / "inputs" / "hello.json", pretty_json_line({"name": "World"}))
    _write(
        root / "inputs" / "artifact.json",
        pretty_json_line({"filename": "report.txt", "message": "Artifact proof"}),
    )
    _write(root / "schemas" / "hello.input.schema.json", _hello_schema_json())
    _write(root / "schemas" / "artifact.input.schema.json", _artifact_schema_json())
    _write(root / "workers" / hello_kind / "Dockerfile", _adopter_worker_dockerfile())
    _write(root / "workers" / hello_kind / "worker.py", _adopter_hello_worker())
    _write(root / "workers" / hello_kind / ".dockerignore", "__pycache__/\n*.pyc\n")
    _write(root / "workers" / artifact_kind / "Dockerfile", _adopter_worker_dockerfile())
    _write(root / "workers" / artifact_kind / "worker.py", _adopter_artifact_worker())
    _write(root / "workers" / artifact_kind / ".dockerignore", "__pycache__/\n*.pyc\n")
    _write(root / "README.md", _adopter_project_readme(prefix))
    return root


def _write(path: Path, content: str) -> None:
    """Write a generated text file with UTF-8 encoding."""
    path.write_text(content, encoding="utf-8")


def _pyproject(package_name: str, kind: str, long_service_kind: str | None) -> str:
    """Return pyproject metadata with a goblin entry point."""
    long_entry = (
        f'{long_service_kind.replace(".", "_").replace("-", "_")} = '
        f'"{package_name}.long_service:definition"'
        if long_service_kind is not None
        else ""
    )
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
{long_entry}

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

def _long_service_module(kind: str) -> str:
    """Return generated long-service goblin definition module content."""
    display = kind.replace(".", " ").replace("-", " ").title()
    return f'''"""Generated long-running service goblin definition."""

from __future__ import annotations

from datetime import UTC, datetime

from goblin_king import GoblinContext, GoblinDefinition, GoblinResult

GOBLIN_KIND = "{kind}"


def definition() -> GoblinDefinition:
    """Return the Goblin King package entry point definition."""
    return GoblinDefinition(
        kind=GOBLIN_KIND,
        display_name="{display}",
        module=__name__,
        entrypoint="run",
        timeout_seconds=10,
        max_retries=0,
    )


def run(input_payload: dict, ctx: GoblinContext) -> GoblinResult:
    """Return a timestamped response for local in-process debugging."""
    return GoblinResult.ok(
        data={{
            "message": "Hello World from long running service",
            "timestamp": datetime.now(UTC).isoformat(),
            "input": input_payload,
            "run_id": ctx.run_id,
        }}
    )
'''

def _test_module(
    package_name: str,
    kind: str,
    long_service_kind: str,
    include_long_service: bool,
) -> str:
    """Return generated package test content."""
    service_import = (
        f"from {package_name}.long_service import definition as service_definition\n"
        if include_long_service
        else ""
    )
    service_test = (
        f'''

def test_long_service_definition_kind() -> None:
    """Verify the long-service entry point definition uses the expected kind."""
    assert service_definition().kind == "{long_service_kind}"
'''
        if include_long_service
        else ""
    )
    return f'''"""Tests for the generated {kind} goblin."""

from {package_name}.goblin import GOBLIN_KIND, definition
{service_import}


def test_definition_kind() -> None:
    """Verify the package entry point definition uses the expected kind."""
    assert GOBLIN_KIND == "{kind}"
    assert definition().kind == "{kind}"
{service_test}'''


def _registry_json(
    kind: str,
    module_name: str,
    long_service_kind: str | None,
    service_module_name: str,
) -> str:
    """Return generated registry stub JSON."""
    goblins = [
        {
            "kind": kind,
            "display_name": kind,
            "module": module_name,
            "entrypoint": "run",
            "timeout_seconds": 30,
            "max_retries": 0,
        }
    ]
    if long_service_kind is not None:
        goblins.append(
            {
                "kind": long_service_kind,
                "display_name": long_service_kind,
                "module": service_module_name,
                "entrypoint": "run",
                "timeout_seconds": 10,
                "max_retries": 0,
            }
        )
    return json.dumps(
        {"goblins": goblins},
        indent=2,
    ) + "\n"


def _images_json(
    kind: str,
    image: str,
    long_service_kind: str | None,
    long_service_image: str,
) -> str:
    """Return generated worker image map JSON."""
    workers = {
        kind: {
            "context": f"workers/{kind}",
            "dockerfile": "Dockerfile",
            "image": image,
        }
    }
    if long_service_kind is not None:
        workers[long_service_kind] = {
            "context": f"workers/{long_service_kind}",
            "dockerfile": "Dockerfile",
            "image": long_service_image,
        }
    return json.dumps(
        {"workers": workers},
        indent=2,
    ) + "\n"


def _project_settings_json() -> str:
    """Return generated project integration settings."""
    return json.dumps(
        {
            "registries": [],
            "entry_points": True,
            "images": "goblin-images.json",
            "api_settings": "goblin-king-api.json",
        },
        indent=2,
    ) + "\n"


def _api_settings_json() -> str:
    """Return generated local API settings for host-project proof."""
    return json.dumps(
        {
            "project": "goblin-king-project.json",
            "registry": "goblins.json",
            "images": "goblin-images.json",
            "db": ".goblin-king/goblin-king.sqlite3",
            "redis_url": "redis://localhost:6379/0",
            "artifact_root": ".goblin-king/artifacts",
            "bootstrap_admin_token": "local-dev-token",
        },
        indent=2,
    ) + "\n"


def _adopter_project_json(prefix: str) -> str:
    """Return a versioned GoblinProject template with two container goblins."""
    hello_kind = f"{prefix}.hello"
    artifact_kind = f"{prefix}.artifact"
    return json.dumps(
        {
            "apiVersion": PROJECT_CONFIG_API_VERSION,
            "kind": PROJECT_CONFIG_KIND,
            "registries": [],
            "entry_points": False,
            "images": "goblin-images.json",
            "api_settings": "goblin-king-api.json",
            "goblins": {
                hello_kind: {
                    "image": f"{prefix}-hello:local",
                    "context": f"workers/{hello_kind}",
                    "dockerfile": "Dockerfile",
                    "description": "Minimal project-owned hello goblin.",
                    "inputSchema": "schemas/hello.input.schema.json",
                    "labels": {"demo": "true", "kind": "hello"},
                    "tags": ["quickstart", "success"],
                    "resourcePolicy": {
                        "timeout_seconds": 30,
                        "memory": {"limit": "256Mi"},
                    },
                },
                artifact_kind: {
                    "image": f"{prefix}-artifact:local",
                    "context": f"workers/{artifact_kind}",
                    "dockerfile": "Dockerfile",
                    "description": "Project-owned artifact-producing goblin.",
                    "inputSchema": "schemas/artifact.input.schema.json",
                    "artifacts": {"enabled": True},
                    "labels": {"demo": "true", "kind": "artifact"},
                    "tags": ["quickstart", "artifact"],
                    "resourcePolicy": {
                        "timeout_seconds": 30,
                        "memory": {"limit": "256Mi"},
                    },
                },
            },
        },
        indent=2,
    ) + "\n"


def _hello_schema_json() -> str:
    """Return the minimal hello input schema for generated projects."""
    return json.dumps(
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "additionalProperties": True,
        },
        indent=2,
    ) + "\n"


def _artifact_schema_json() -> str:
    """Return the minimal artifact input schema for generated projects."""
    return json.dumps(
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {
                "filename": {"type": "string"},
                "message": {"type": "string"},
            },
            "additionalProperties": True,
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
from datetime import UTC, datetime
from pathlib import Path

from redis import Redis


def main() -> None:
    """Read worker inputs, publish a result to Redis, and write fallback output."""
    input_payload = json.loads(Path(os.environ["GOBLIN_INPUT_PATH"]).read_text())
    context = json.loads(Path(os.environ["GOBLIN_CONTEXT_PATH"]).read_text())
    result_path = Path(os.environ["GOBLIN_RESULT_PATH"])
    run_id = os.environ["GOBLIN_RUN_ID"]
    job_id = os.environ.get("GOBLIN_JOB_ID") or None
    worker_id = os.environ["GOBLIN_WORKER_ID"]
    heartbeat_url = os.environ["GOBLIN_HEARTBEAT_REDIS_URL"]

    _heartbeat("running", heartbeat_url, worker_id, run_id, job_id)
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
    _heartbeat("completed", heartbeat_url, worker_id, run_id, job_id)


def _heartbeat(
    status: str,
    redis_url: str,
    worker_id: str,
    run_id: str,
    job_id: str | None,
) -> None:
    """Publish a worker heartbeat through Redis for the host runtime to persist."""
    payload = {
        "owner_id": worker_id,
        "owner_type": "worker",
        "status": status,
        "last_seen_at": datetime.now(UTC).isoformat(),
        "job_id": job_id,
        "run_id": run_id,
        "payload": {"generated": True},
    }
    encoded = json.dumps(payload)
    client = Redis.from_url(redis_url)
    client.rpush(os.environ["GOBLIN_HEARTBEAT_KEY"], encoded)
    client.publish(os.environ["GOBLIN_HEARTBEAT_CHANNEL"], encoded)


if __name__ == "__main__":
    main()
'''


def _adopter_worker_dockerfile() -> str:
    """Return a tiny Python worker Dockerfile for project templates."""
    return """FROM python:3.12-slim

WORKDIR /worker
COPY worker.py /worker/worker.py
RUN pip install --no-cache-dir "redis>=5,<7"

ENTRYPOINT ["python", "/worker/worker.py"]
"""


def _adopter_hello_worker() -> str:
    """Return a contract-compliant hello worker for project templates."""
    return '''"""Minimal generated project goblin worker."""

from __future__ import annotations

import json
import os
from pathlib import Path

from redis import Redis


def main() -> None:
    """Read input/context files and write a successful result envelope."""
    input_payload = json.loads(Path(os.environ["GOBLIN_INPUT_PATH"]).read_text())
    context = json.loads(Path(os.environ["GOBLIN_CONTEXT_PATH"]).read_text())
    name = input_payload.get("name", "World")
    result = {
        "status": "success",
        "data": {
            "message": f"Hello {name}",
            "input": input_payload,
            "run_id": context["run_id"],
        },
        "artifacts": [],
        "metrics": {"template": "hello"},
        "handoff": [],
        "error": None,
    }
    _write_result(result)


def _write_result(result: dict) -> None:
    """Write the fallback result file and publish the same envelope to Redis."""
    result_json = json.dumps(result)
    Path(os.environ["GOBLIN_RESULT_PATH"]).write_text(result_json, encoding="utf-8")
    Redis.from_url(os.environ["GOBLIN_REDIS_URL"]).set(
        f"goblin-king:results:{os.environ['GOBLIN_RUN_ID']}",
        result_json,
        ex=3600,
    )


if __name__ == "__main__":
    main()
'''


def _adopter_artifact_worker() -> str:
    """Return a contract-compliant artifact worker for project templates."""
    return '''"""Generated project goblin worker that writes one artifact."""

from __future__ import annotations

import json
import os
from pathlib import Path

from redis import Redis


def main() -> None:
    """Create a text artifact and return matching artifact metadata."""
    input_payload = json.loads(Path(os.environ["GOBLIN_INPUT_PATH"]).read_text())
    context = json.loads(Path(os.environ["GOBLIN_CONTEXT_PATH"]).read_text())
    artifact_root = Path(os.environ["GOBLIN_ARTIFACT_ROOT"])
    artifact_root.mkdir(parents=True, exist_ok=True)
    artifact_name = input_payload.get("filename", "report.txt")
    artifact_path = artifact_root / artifact_name
    artifact_path.write_text(input_payload.get("message", "Artifact proof"), encoding="utf-8")
    result = {
        "status": "success",
        "data": {
            "message": "artifact written",
            "run_id": context["run_id"],
        },
        "artifacts": [
            {
                "name": artifact_name,
                "uri": f"artifact://{artifact_name}",
                "content_type": "text/plain",
                "metadata": {"source": "project-template"},
            }
        ],
        "metrics": {"artifact_count": 1},
        "handoff": [],
        "error": None,
    }
    _write_result(result)


def _write_result(result: dict) -> None:
    """Write the fallback result file and publish the same envelope to Redis."""
    result_json = json.dumps(result)
    Path(os.environ["GOBLIN_RESULT_PATH"]).write_text(result_json, encoding="utf-8")
    Redis.from_url(os.environ["GOBLIN_REDIS_URL"]).set(
        f"goblin-king:results:{os.environ['GOBLIN_RUN_ID']}",
        result_json,
        ex=3600,
    )


if __name__ == "__main__":
    main()
'''


def _service_dockerfile() -> str:
    """Return a minimal Python HTTP service worker Dockerfile."""
    return """FROM python:3.12-slim

WORKDIR /worker
COPY service.py /worker/service.py

ENTRYPOINT ["python", "/worker/service.py"]
"""


def _service_module(kind: str) -> str:
    """Return generated long-running service worker code."""
    return f'''"""Generated long-running service worker for Goblin King admin probes."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

GOBLIN_KIND = "{kind}"


class Handler(BaseHTTPRequestHandler):
    """Serve timestamped proof responses for long-running service checks."""

    def do_GET(self) -> None:
        """Return a JSON liveness response."""
        payload = {{
            "kind": GOBLIN_KIND,
            "message": "Hello World from long running service",
            "timestamp": datetime.now(UTC).isoformat(),
        }}
        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def main() -> None:
    """Run the generated service on port 8080."""
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()


if __name__ == "__main__":
    main()
'''


def _readme(kind: str, package_name: str, image: str, include_long_service: bool) -> str:
    """Return generated package README content."""
    service = (
        f"""
## Long-Running Service

This package also includes `{kind}.long-service` with a self-contained HTTP service
worker folder at `workers/{kind}.long-service`.
"""
        if include_long_service
        else ""
    )
    return f"""# {package_name}

Generated Goblin King package for `{kind}`.

- Python entry point group: `goblin_king.goblins`
- Worker image: `{image}`
- Worker folder: `workers/{kind}`
- Registry stub: `goblins.json` for JSON-based integration
- Worker image map: `goblin-images.json`
- Project settings: `goblin-king-project.json` uses entry-point discovery by default
- API settings: `goblin-king-api.json`
{service}

## Local Proof

```bash
python -m pip install -e .
python -m pytest
goblin-king project validate --project goblin-king-project.json
goblin-king workers build --images goblin-images.json
```
"""


def _adopter_project_readme(prefix: str) -> str:
    """Return the golden-path README for generated adopter projects."""
    hello_kind = f"{prefix}.hello"
    artifact_kind = f"{prefix}.artifact"
    return f"""# Goblin King Project Template

This generated project defines contract-compliant container goblins without editing
Goblin King source code.

## Files

- `goblin-king-project.json`: versioned `GoblinProject` config.
- `goblin-images.json`: empty base image map; inline goblins provide their own image
  settings.
- `workers/{hello_kind}/`: short-running hello worker.
- `workers/{artifact_kind}/`: artifact-producing worker.
- `inputs/`: sample JSON inputs.
- `schemas/`: optional input schemas for humans and tooling.

## Golden Path

```bash
python -m goblin_king.cli project validate --project goblin-king-project.json

python -m goblin_king.cli workers validate \\
  --project goblin-king-project.json \\
  --input inputs/hello.json \\
  --kind {hello_kind} \\
  --build \\
  --require-success

python -m goblin_king.cli workers validate \\
  --project goblin-king-project.json \\
  --input inputs/artifact.json \\
  --kind {artifact_kind} \\
  --build \\
  --require-success
```

To run through the scheduler/API stack, mount or bake this project config into the
Goblin King services and reload discovery. The admin will show `{hello_kind}` and
`{artifact_kind}` without a React rebuild.

The King asks for proof, and this template hands him a tidy stack of receipts.
"""


def _long_service_image(image: str) -> str:
    """Derive a long-service image name from the short worker image tag."""
    if ":" in image.rsplit("/", 1)[-1]:
        name, tag = image.rsplit(":", 1)
        return f"{name}-long:{tag}"
    return f"{image}-long"
