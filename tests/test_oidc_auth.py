"""Tests for OIDC/JWT bearer authentication."""

from __future__ import annotations

from pathlib import Path

import pytest

from goblin_king.api import create_app
from goblin_king.api_settings import ApiSettings, OidcSettings
from goblin_king.auth import AuthError, authenticate_token
from goblin_king.store import SQLiteStore


def test_oidc_token_maps_claims_to_principal(tmp_path: Path, monkeypatch) -> None:
    """Verify validated OIDC claims become project-scoped principals."""
    oidc = OidcSettings(
        enabled=True,
        issuer="https://issuer.example",
        audience="goblin-king",
        jwks_url="https://issuer.example/.well-known/jwks.json",
    )
    monkeypatch.setattr(
        "goblin_king.auth._decode_oidc_jwt",
        lambda _token, _oidc: {
            "sub": "user-1",
            "goblin_king_role": "admin",
            "goblin_king_project_id": "project-1",
        },
    )

    principal = authenticate_token(
        SQLiteStore(tmp_path / "auth.sqlite3"),
        "oidc.jwt.token",
        bootstrap_token="local-dev-token",
        oidc=oidc,
    )

    assert principal.auth_provider == "oidc"
    assert principal.user_id == "user-1"
    assert principal.role == "admin"
    assert principal.project_id == "project-1"
    assert principal.is_admin


def test_oidc_invalid_token_returns_auth_error(tmp_path: Path, monkeypatch) -> None:
    """Verify invalid OIDC tokens fail after local token lookup misses."""
    oidc = OidcSettings(
        enabled=True,
        issuer="https://issuer.example",
        audience="goblin-king",
        jwks_url="https://issuer.example/.well-known/jwks.json",
    )

    def fail_decode(_token: str, _oidc: OidcSettings) -> dict:
        raise AuthError("missing or invalid OIDC bearer token", status_code=401)

    monkeypatch.setattr("goblin_king.auth._decode_oidc_jwt", fail_decode)

    with pytest.raises(AuthError, match="OIDC"):
        authenticate_token(
            SQLiteStore(tmp_path / "auth.sqlite3"),
            "bad.jwt.token",
            bootstrap_token="local-dev-token",
            oidc=oidc,
        )


def test_local_token_precedence_over_oidc(tmp_path: Path, monkeypatch) -> None:
    """Verify the bootstrap/local token path wins before OIDC decoding is attempted."""
    oidc = OidcSettings(enabled=True, issuer="issuer", audience="audience", jwks_url="jwks")

    def fail_if_called(_token: str, _oidc: OidcSettings) -> dict:
        raise AssertionError("OIDC should not decode bootstrap tokens")

    monkeypatch.setattr("goblin_king.auth._decode_oidc_jwt", fail_if_called)

    principal = authenticate_token(
        SQLiteStore(tmp_path / "auth.sqlite3"),
        "local-dev-token",
        bootstrap_token="local-dev-token",
        oidc=oidc,
    )

    assert principal.bootstrap is True
    assert principal.auth_provider == "local"


def test_websocket_accepts_oidc_token(tmp_path: Path, monkeypatch) -> None:
    """Verify WebSocket auth uses the same OIDC path as HTTP auth."""
    settings = ApiSettings(
        registry=Path("examples/goblins.json").resolve(),
        images=Path("goblin-images.json").resolve(),
        db=tmp_path / "api.sqlite3",
        artifact_root=tmp_path / "artifacts",
        oidc=OidcSettings(
            enabled=True,
            issuer="https://issuer.example",
            audience="goblin-king",
            jwks_url="https://issuer.example/.well-known/jwks.json",
        ),
    )
    monkeypatch.setattr(
        "goblin_king.auth._decode_oidc_jwt",
        lambda _token, _oidc: {"sub": "user-1", "goblin_king_role": "member"},
    )

    class FakePubSub:
        def subscribe(self, _channel: str) -> None:
            return None

        def get_message(self, *_args) -> dict | None:
            return {"type": "message", "data": b'{"event_type":"job.completed"}'}

        def close(self) -> None:
            return None

    class FakeRedis:
        def pubsub(self) -> FakePubSub:
            return FakePubSub()

    monkeypatch.setattr("goblin_king.api.Redis.from_url", lambda _url: FakeRedis())

    from fastapi.testclient import TestClient

    with TestClient(create_app(settings)).websocket_connect("/ws/runs?token=oidc.jwt") as ws:
        assert ws.receive_json()["event_type"] == "job.completed"
