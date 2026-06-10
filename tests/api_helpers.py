"""Shared API test setup helpers for isolated local control-plane tests."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from goblin_king.api import create_app
from goblin_king.api_settings import ApiSettings
from goblin_king.store import SQLiteStore


def build_api_client(
    tmp_path: Path,
    *,
    rate_limit_per_minute: int | None = None,
) -> tuple[TestClient, SQLiteStore, Path]:
    """Create a test API app with isolated SQLite and artifact storage."""
    artifact_root = tmp_path / "artifacts"
    settings = ApiSettings(
        registry=Path("examples/goblins.json").resolve(),
        images=Path("goblin-images.json").resolve(),
        db=tmp_path / "api.sqlite3",
        redis_url="redis://localhost:6379/0",
        artifact_root=artifact_root,
        auth_token="test-token",
        rate_limit_per_minute=rate_limit_per_minute or 60,
    )
    return TestClient(create_app(settings)), SQLiteStore(settings.db), artifact_root


def auth_headers() -> dict[str, str]:
    """Return the static bearer token used by local API tests."""
    return {"Authorization": "Bearer test-token"}
