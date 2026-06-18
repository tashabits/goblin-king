"""ASGI runner for notebook-authored service bundles."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path


def main() -> None:
    """Install inline requirements, import the ASGI app, and serve it."""
    command = sys.argv[1] if len(sys.argv) > 1 else "serve"
    source_path = Path(
        os.environ.get("GOBLIN_NOTEBOOK_SERVICE_SOURCE", "/goblin-service/source.py")
    )
    requirements_path = Path(
        os.environ.get("GOBLIN_NOTEBOOK_SERVICE_REQUIREMENTS", "/goblin-service/requirements.txt")
    )
    app_name = os.environ.get("GOBLIN_NOTEBOOK_SERVICE_APP", "app")
    port = int(os.environ.get("PORT", "8080"))
    _install_requirements(requirements_path)
    app = _load_app(source_path, app_name)
    if command == "validate":
        print(f"validated ASGI app {source_path}:{app_name}")
        return
    if command != "serve":
        raise SystemExit(f"unsupported command: {command}")
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")


def _install_requirements(path: Path) -> None:
    if not path.exists() or not path.read_text(encoding="utf-8").strip():
        return
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "--no-cache-dir", "-r", str(path)],
        check=True,
    )


def _load_app(source_path: Path, app_name: str):
    if not source_path.exists():
        raise SystemExit(f"service source does not exist: {source_path}")
    spec = importlib.util.spec_from_file_location("notebook_service_source", source_path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"could not import service source: {source_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    if not hasattr(module, app_name):
        raise SystemExit(f"ASGI app symbol not found: {app_name}")
    app = getattr(module, app_name)
    if not callable(app):
        raise SystemExit(f"ASGI app symbol is not callable: {app_name}")
    return app


if __name__ == "__main__":
    main()
