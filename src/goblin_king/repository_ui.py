"""Browser-facing Goblin Repository service for JupyterHub deployments."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from goblin_king.repository_bundles import (
    RepositoryBundleError,
    RepositoryBundleLimits,
    parse_repository_bundle,
)

ALLOWED_PROXY_PATHS = (
    "/repository/",
    "/jobs",
    "/jobs/",
    "/runs",
    "/runs/",
)
HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}


class RepositoryUISettings(BaseModel):
    """Runtime settings for the Hub-authenticated repository UI service."""

    api_url: str = "http://goblin-king-api:8000"
    repository_url: str | None = None
    hub_api_url: str = "http://hub.default.svc.cluster.local:8081/hub/api"
    hub_base_url: str = "/hub/"
    public_url: str | None = None
    service_name: str = "goblin-repository"
    service_prefix: str = "/services/goblin-repository/"
    service_token: str | None = None
    service_token_env: str = "GOBLIN_KING_REPOSITORY_UI_SERVICE_TOKEN"
    request_timeout_seconds: float = Field(default=10.0, gt=0)
    session_ttl_seconds: int = Field(default=8 * 60 * 60, gt=0)
    state_ttl_seconds: int = Field(default=10 * 60, gt=0)
    session_cookie_name: str = "goblin_repository_session"
    state_cookie_name: str = "goblin_repository_oauth_state"
    admin_groups: list[str] = Field(default_factory=lambda: ["goblin-admins"])
    static_root: Path | None = None
    max_bundle_bytes: int = 5 * 1024 * 1024
    max_source_bytes: int = 1024 * 1024
    max_requirements_bytes: int = 64 * 1024
    max_files: int = 50

    @classmethod
    def from_env(cls) -> RepositoryUISettings:
        """Load service settings from environment variables."""
        payload: dict[str, Any] = {}
        mappings = {
            "api_url": "GOBLIN_KING_REPOSITORY_UI_API_URL",
            "repository_url": "GOBLIN_KING_REPOSITORY_UI_REPOSITORY_URL",
            "hub_api_url": "GOBLIN_KING_REPOSITORY_UI_HUB_API_URL",
            "hub_base_url": "GOBLIN_KING_REPOSITORY_UI_HUB_BASE_URL",
            "public_url": "GOBLIN_KING_REPOSITORY_UI_PUBLIC_URL",
            "service_name": "GOBLIN_KING_REPOSITORY_UI_SERVICE_NAME",
            "service_prefix": "GOBLIN_KING_REPOSITORY_UI_SERVICE_PREFIX",
            "service_token_env": "GOBLIN_KING_REPOSITORY_UI_SERVICE_TOKEN_ENV",
            "session_cookie_name": "GOBLIN_KING_REPOSITORY_UI_SESSION_COOKIE_NAME",
            "state_cookie_name": "GOBLIN_KING_REPOSITORY_UI_STATE_COOKIE_NAME",
        }
        for field_name, env_name in mappings.items():
            value = os.environ.get(env_name)
            if value:
                payload[field_name] = value
        service_token = os.environ.get("GOBLIN_KING_REPOSITORY_UI_SERVICE_TOKEN")
        if service_token:
            payload["service_token"] = service_token
        token_env = payload.get("service_token_env")
        if isinstance(token_env, str) and token_env:
            env_token = os.environ.get(token_env)
            if env_token:
                payload["service_token"] = env_token
        admin_groups = os.environ.get("GOBLIN_KING_REPOSITORY_UI_ADMIN_GROUPS")
        if admin_groups:
            payload["admin_groups"] = _env_list(admin_groups)
        static_root = os.environ.get("GOBLIN_KING_REPOSITORY_UI_STATIC_ROOT")
        if static_root:
            payload["static_root"] = Path(static_root)
        for field_name, env_name in {
            "request_timeout_seconds": "GOBLIN_KING_REPOSITORY_UI_REQUEST_TIMEOUT_SECONDS",
            "session_ttl_seconds": "GOBLIN_KING_REPOSITORY_UI_SESSION_TTL_SECONDS",
            "state_ttl_seconds": "GOBLIN_KING_REPOSITORY_UI_STATE_TTL_SECONDS",
            "max_bundle_bytes": "GOBLIN_KING_REPOSITORY_UI_MAX_BUNDLE_BYTES",
            "max_source_bytes": "GOBLIN_KING_REPOSITORY_UI_MAX_SOURCE_BYTES",
            "max_requirements_bytes": "GOBLIN_KING_REPOSITORY_UI_MAX_REQUIREMENTS_BYTES",
            "max_files": "GOBLIN_KING_REPOSITORY_UI_MAX_FILES",
        }.items():
            value = os.environ.get(env_name)
            if value:
                payload[field_name] = float(value) if "seconds" in field_name else int(value)
        return cls.model_validate(payload)

    @property
    def normalized_prefix(self) -> str:
        """Return the service prefix without a trailing slash for route registration."""
        return "/" + self.service_prefix.strip("/")

    @property
    def client_id(self) -> str:
        """Return the JupyterHub OAuth client id for this service."""
        return f"service-{self.service_name}"

    @property
    def bundle_limits(self) -> RepositoryBundleLimits:
        """Return upload parser limits."""
        return RepositoryBundleLimits(
            max_bundle_bytes=self.max_bundle_bytes,
            max_source_bytes=self.max_source_bytes,
            max_requirements_bytes=self.max_requirements_bytes,
            max_files=self.max_files,
        )


@dataclass(frozen=True)
class RepositoryUISession:
    """Server-side session data keyed by a signed browser cookie."""

    session_id: str
    user_name: str
    token: str
    groups: tuple[str, ...]
    is_admin: bool
    expires_at: float


class RepositoryUISessionStore:
    """Small in-memory session store for a single service replica."""

    def __init__(self) -> None:
        self._sessions: dict[str, RepositoryUISession] = {}

    def create(
        self,
        *,
        user_name: str,
        token: str,
        groups: list[str],
        is_admin: bool,
        ttl_seconds: int,
    ) -> RepositoryUISession:
        self.cleanup()
        session = RepositoryUISession(
            session_id=secrets.token_urlsafe(32),
            user_name=user_name,
            token=token,
            groups=tuple(groups),
            is_admin=is_admin,
            expires_at=time.time() + ttl_seconds,
        )
        self._sessions[session.session_id] = session
        return session

    def get(self, session_id: str) -> RepositoryUISession | None:
        session = self._sessions.get(session_id)
        if session is None:
            return None
        if session.expires_at <= time.time():
            self._sessions.pop(session_id, None)
            return None
        return session

    def delete(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    def cleanup(self) -> None:
        now = time.time()
        expired = [
            session_id
            for session_id, session in self._sessions.items()
            if session.expires_at <= now
        ]
        for session_id in expired:
            self._sessions.pop(session_id, None)


def create_repository_ui_app(
    settings: RepositoryUISettings | None = None,
    *,
    session_store: RepositoryUISessionStore | None = None,
) -> FastAPI:
    """Create the repository UI service app."""
    settings = settings or RepositoryUISettings.from_env()
    session_store = session_store or RepositoryUISessionStore()
    prefix = settings.normalized_prefix
    app = FastAPI(title="Goblin Repository UI")

    @app.get(f"{prefix}/oauth_callback", name="repository_oauth_callback")
    def oauth_callback(request: Request, code: str, state: str) -> Response:
        oauth_state = _decode_signed_json_cookie(
            request.cookies.get(settings.state_cookie_name),
            settings,
        )
        if not oauth_state or oauth_state.get("state") != state:
            raise HTTPException(status_code=400, detail="invalid OAuth state")
        if float(oauth_state.get("expires_at", 0)) <= time.time():
            raise HTTPException(status_code=400, detail="expired OAuth state")
        token = _exchange_oauth_code(
            settings,
            code=code,
            redirect_uri=_callback_url(request, settings),
        )
        user = _identify_hub_user(settings, token)
        user_name = user.get("name")
        if not isinstance(user_name, str) or not user_name:
            raise HTTPException(status_code=502, detail="Hub user response missing name")
        user_detail = _load_hub_user_detail(settings, user_name)
        groups = _hub_groups(user_detail or user)
        session = session_store.create(
            user_name=user_name,
            token=token,
            groups=groups,
            is_admin=bool(set(groups) & set(settings.admin_groups)),
            ttl_seconds=settings.session_ttl_seconds,
        )
        response = RedirectResponse(str(oauth_state.get("next") or f"{prefix}/"))
        response.set_cookie(
            settings.session_cookie_name,
            _signed_value(session.session_id, settings),
            httponly=True,
            secure=False,
            samesite="lax",
            max_age=settings.session_ttl_seconds,
            path=settings.normalized_prefix,
        )
        response.delete_cookie(settings.state_cookie_name, path=settings.normalized_prefix)
        return response

    @app.get(f"{prefix}/ui-api/me")
    def me(request: Request) -> dict[str, Any]:
        session = _require_session(request, settings, session_store)
        return {
            "user": session.user_name,
            "groups": list(session.groups),
            "is_admin": session.is_admin,
            "service_prefix": settings.service_prefix,
            "repository_url": settings.repository_url or settings.api_url,
        }

    @app.post(f"{prefix}/ui-api/logout")
    def logout(request: Request) -> Response:
        session_id = _decode_signed_value(
            request.cookies.get(settings.session_cookie_name),
            settings,
        )
        if session_id:
            session_store.delete(session_id)
        response = JSONResponse({"ok": True})
        response.delete_cookie(settings.session_cookie_name, path=settings.normalized_prefix)
        return response

    @app.post(f"{prefix}/ui-api/bundles/preview")
    async def preview_bundle(request: Request) -> dict[str, Any]:
        _require_session(request, settings, session_store)
        data = await request.body()
        try:
            preview = parse_repository_bundle(data, limits=settings.bundle_limits)
        except (RepositoryBundleError, ValueError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return preview.model_dump(mode="json")

    @app.post(f"{prefix}/ui-api/bundles/submit")
    async def submit_bundle(request: Request) -> Response:
        session = _require_session(request, settings, session_store)
        data = await request.body()
        try:
            preview = parse_repository_bundle(data, limits=settings.bundle_limits)
        except (RepositoryBundleError, ValueError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return _forward_json(
            settings,
            session,
            "POST",
            "/repository/entries",
            preview.submit_payload,
        )

    @app.api_route(
        f"{prefix}/ui-api/{{path:path}}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    )
    async def proxy_api(path: str, request: Request) -> Response:
        session = _require_session(request, settings, session_store)
        api_path = f"/{path}"
        if not _proxy_path_allowed(api_path):
            raise HTTPException(status_code=404, detail="repository UI path is not proxied")
        body = await request.body()
        return _forward_raw(
            settings,
            session,
            request.method,
            api_path,
            query=request.url.query,
            body=body if body else None,
            content_type=request.headers.get("content-type"),
        )

    @app.get(f"{prefix}")
    def service_prefix_redirect() -> Response:
        return RedirectResponse(f"{prefix}/")

    @app.get(f"{prefix}/")
    def index(request: Request) -> Response:
        session = _optional_session(request, settings, session_store)
        if session is None:
            return _oauth_redirect(request, settings)
        return _static_response(settings, "index.html")

    @app.get(f"{prefix}/{{path:path}}")
    def static_or_index(path: str, request: Request) -> Response:
        session = _optional_session(request, settings, session_store)
        if session is None:
            return _oauth_redirect(request, settings)
        return _static_response(settings, path or "index.html")

    return app


def run_repository_ui(
    *,
    host: str = "127.0.0.1",
    port: int = 8080,
    settings: RepositoryUISettings | None = None,
) -> None:
    """Run the repository UI service with Uvicorn."""
    import uvicorn

    uvicorn.run(create_repository_ui_app(settings), host=host, port=port)


def _oauth_redirect(request: Request, settings: RepositoryUISettings) -> Response:
    if not settings.service_token:
        raise HTTPException(status_code=500, detail="repository UI service token is required")
    state = secrets.token_urlsafe(32)
    next_path = request.url.path
    if request.url.query:
        next_path = f"{next_path}?{request.url.query}"
    state_payload = {
        "state": state,
        "next": next_path,
        "expires_at": time.time() + settings.state_ttl_seconds,
    }
    params = {
        "client_id": settings.client_id,
        "redirect_uri": _callback_url(request, settings),
        "response_type": "code",
        "state": state,
    }
    response = RedirectResponse(
        f"{_hub_oauth_authorize_url(settings)}?{urlparse.urlencode(params)}"
    )
    response.set_cookie(
        settings.state_cookie_name,
        _encode_signed_json(state_payload, settings),
        httponly=True,
        secure=False,
        samesite="lax",
        max_age=settings.state_ttl_seconds,
        path=settings.normalized_prefix,
    )
    return response


def _exchange_oauth_code(
    settings: RepositoryUISettings,
    *,
    code: str,
    redirect_uri: str,
) -> str:
    if not settings.service_token:
        raise HTTPException(status_code=500, detail="repository UI service token is required")
    data = urlparse.urlencode(
        {
            "client_id": settings.client_id,
            "client_secret": settings.service_token,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
        }
    ).encode("utf-8")
    request = urlrequest.Request(
        f"{settings.hub_api_url.rstrip('/')}/oauth2/token",
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urlrequest.urlopen(request, timeout=settings.request_timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urlerror.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise HTTPException(
            status_code=502,
            detail=f"Hub OAuth token exchange failed with {error.code}: {detail}",
        ) from error
    except json.JSONDecodeError as error:
        raise HTTPException(
            status_code=502,
            detail=f"Hub OAuth token exchange returned invalid JSON: {error}",
        ) from error
    except OSError as error:
        raise HTTPException(
            status_code=502,
            detail=f"Hub OAuth token exchange failed: {error}",
        ) from error
    token = payload.get("access_token")
    if not isinstance(token, str) or not token:
        raise HTTPException(status_code=502, detail="Hub OAuth response missing access token")
    return token


def _identify_hub_user(settings: RepositoryUISettings, token: str) -> dict[str, Any]:
    return _hub_json(
        settings,
        f"{settings.hub_api_url.rstrip('/')}/user",
        token=token,
        invalid_message="Hub OAuth token is invalid",
    )


def _load_hub_user_detail(settings: RepositoryUISettings, user_name: str) -> dict[str, Any] | None:
    if not settings.service_token:
        return None
    try:
        return _hub_json(
            settings,
            f"{settings.hub_api_url.rstrip('/')}/users/{urlparse.quote(user_name, safe='')}",
            token=settings.service_token,
            invalid_message="repository UI service token is invalid",
        )
    except HTTPException as error:
        if error.status_code in {401, 403, 404, 502, 503}:
            return None
        raise


def _hub_json(
    settings: RepositoryUISettings,
    url: str,
    *,
    token: str,
    invalid_message: str,
) -> dict[str, Any]:
    request = urlrequest.Request(
        url,
        headers={"Accept": "application/json", "Authorization": f"token {token}"},
    )
    try:
        with urlrequest.urlopen(request, timeout=settings.request_timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urlerror.HTTPError as error:
        if error.code in {401, 403, 404}:
            raise HTTPException(status_code=401, detail=invalid_message) from error
        raise HTTPException(status_code=503, detail="Hub API request failed") from error
    except (OSError, json.JSONDecodeError) as error:
        raise HTTPException(status_code=503, detail="Hub API is unavailable") from error
    if not isinstance(payload, dict):
        raise HTTPException(status_code=503, detail="Hub API response was not an object")
    user = payload.get("user")
    return user if isinstance(user, dict) else payload


def _forward_json(
    settings: RepositoryUISettings,
    session: RepositoryUISession,
    method: str,
    path: str,
    payload: dict[str, Any],
) -> Response:
    return _forward_raw(
        settings,
        session,
        method,
        path,
        body=json.dumps(payload).encode("utf-8"),
        content_type="application/json",
    )


def _forward_raw(
    settings: RepositoryUISettings,
    session: RepositoryUISession,
    method: str,
    path: str,
    *,
    query: str = "",
    body: bytes | None = None,
    content_type: str | None = None,
) -> Response:
    base_url = _target_base_url(settings, path)
    url = f"{base_url.rstrip('/')}{path}"
    if query:
        url = f"{url}?{query}"
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {session.token}",
    }
    if content_type and body is not None:
        headers["Content-Type"] = content_type
    request = urlrequest.Request(url, data=body, headers=headers, method=method)
    try:
        with urlrequest.urlopen(request, timeout=settings.request_timeout_seconds) as response:
            return Response(
                content=response.read(),
                status_code=response.status,
                media_type=response.headers.get("content-type"),
                headers=_response_headers(response.headers.items()),
            )
    except urlerror.HTTPError as error:
        return Response(
            content=error.read(),
            status_code=error.code,
            media_type=error.headers.get("content-type") if error.headers else "application/json",
            headers=_response_headers(error.headers.items()) if error.headers else {},
        )
    except OSError as error:
        raise HTTPException(status_code=503, detail=f"API request failed: {error}") from error


def _target_base_url(settings: RepositoryUISettings, path: str) -> str:
    if path.startswith("/repository/") and settings.repository_url:
        return settings.repository_url
    return settings.api_url


def _response_headers(items: Any) -> dict[str, str]:
    return {
        key: value
        for key, value in dict(items).items()
        if key.lower() not in HOP_BY_HOP_HEADERS
        and key.lower() not in {"content-length", "set-cookie"}
    }


def _static_response(settings: RepositoryUISettings, path: str) -> Response:
    root = settings.static_root or _default_static_root()
    safe_path = path.strip("/") or "index.html"
    if ".." in safe_path.split("/"):
        safe_path = "index.html"
    candidate = (root / safe_path).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        candidate = root / "index.html"
    if candidate.exists() and candidate.is_file():
        return FileResponse(candidate)
    index = root / "index.html"
    if index.exists():
        return FileResponse(index)
    return HTMLResponse(_fallback_html())


def _optional_session(
    request: Request,
    settings: RepositoryUISettings,
    session_store: RepositoryUISessionStore,
) -> RepositoryUISession | None:
    session_id = _decode_signed_value(
        request.cookies.get(settings.session_cookie_name),
        settings,
    )
    return session_store.get(session_id) if session_id else None


def _require_session(
    request: Request,
    settings: RepositoryUISettings,
    session_store: RepositoryUISessionStore,
) -> RepositoryUISession:
    session = _optional_session(request, settings, session_store)
    if session is None:
        raise HTTPException(status_code=401, detail="repository UI login required")
    return session


def _callback_url(request: Request, settings: RepositoryUISettings) -> str:
    callback_path = f"{settings.normalized_prefix}/oauth_callback"
    if settings.public_url:
        return f"{settings.public_url.rstrip('/')}{callback_path}"
    return callback_path


def _hub_oauth_authorize_url(settings: RepositoryUISettings) -> str:
    hub_base = settings.hub_base_url.rstrip("/")
    return f"{hub_base}/api/oauth2/authorize"


def _hub_groups(model: dict[str, Any]) -> list[str]:
    groups = model.get("groups") or []
    if not isinstance(groups, list):
        return []
    names: list[str] = []
    for group in groups:
        if isinstance(group, str):
            names.append(group)
        elif isinstance(group, dict) and isinstance(group.get("name"), str):
            names.append(group["name"])
    return names


def _proxy_path_allowed(path: str) -> bool:
    for allowed in ALLOWED_PROXY_PATHS:
        base = allowed.rstrip("/")
        if path == base or path.startswith(f"{base}/"):
            return True
    return False


def _encode_signed_json(
    payload: dict[str, Any],
    settings: RepositoryUISettings,
) -> str:
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    encoded = base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")
    return _signed_value(encoded, settings)


def _decode_signed_json_cookie(
    value: str | None,
    settings: RepositoryUISettings,
) -> dict[str, Any] | None:
    encoded = _decode_signed_value(value, settings)
    if not encoded:
        return None
    padding = "=" * (-len(encoded) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode((encoded + padding).encode("ascii")))
    except (ValueError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _signed_value(value: str, settings: RepositoryUISettings) -> str:
    signature = hmac.new(
        _cookie_secret(settings),
        value.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{value}.{signature}"


def _decode_signed_value(value: str | None, settings: RepositoryUISettings) -> str | None:
    if not value or "." not in value:
        return None
    raw, signature = value.rsplit(".", 1)
    expected = hmac.new(_cookie_secret(settings), raw.encode("utf-8"), hashlib.sha256).hexdigest()
    return raw if hmac.compare_digest(signature, expected) else None


def _cookie_secret(settings: RepositoryUISettings) -> bytes:
    token = settings.service_token or "local-repository-ui-development-secret"
    return token.encode("utf-8")


def _default_static_root() -> Path:
    packaged = Path(__file__).parent / "repository_ui_static"
    return packaged if packaged.exists() else Path.cwd() / "repository-ui" / "static"


def _fallback_html() -> str:
    return """<!doctype html>
<html lang="en">
  <head><meta charset="utf-8"><title>Goblin Repository</title></head>
  <body><main><h1>Goblin Repository</h1><p>Static UI assets are not installed.</p></main></body>
</html>
"""


def _env_list(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]
