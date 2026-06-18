"""Jupyter Server extension for the in-notebook Goblin Directory picker."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest

DIRECTORY_API_PREFIX = "/goblin-directory/api"
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


@dataclass(frozen=True)
class DirectoryProxyResponse:
    """HTTP response captured from the upstream Directory API."""

    status_code: int
    body: bytes
    content_type: str
    headers: dict[str, str]


class DirectoryProxyError(RuntimeError):
    """Actionable error returned by the Jupyter user-server proxy."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _jupyter_server_extension_points() -> list[dict[str, str]]:
    """Advertise this package as a Jupyter Server extension."""
    return [{"module": "goblin_king.jupyter_directory"}]


def load_jupyter_server_extension(serverapp: Any) -> None:
    """Register Goblin Directory proxy routes on a Jupyter user server."""
    from jupyter_server.base.handlers import JupyterHandler
    from jupyter_server.utils import url_path_join
    from tornado import web

    class DirectoryEntriesHandler(JupyterHandler):
        @web.authenticated
        def get(self) -> None:
            self._proxy_or_error(
                lambda: _forward_directory_request(
                    "GET",
                    "/repository/entries",
                    query=self.request.query,
                )
            )

    class DirectoryFunctionRunHandler(JupyterHandler):
        @web.authenticated
        def post(self, name: str) -> None:
            self._proxy_or_error(
                lambda: _forward_directory_request(
                    "POST",
                    f"/repository/functions/{urlparse.quote(name, safe='')}/run",
                    body=self.request.body or None,
                    content_type=self.request.headers.get("content-type"),
                )
            )

    class DirectoryServiceLifecycleHandler(JupyterHandler):
        @web.authenticated
        def post(self, name: str, action: str) -> None:
            if action not in {"start", "probe", "stop"}:
                raise web.HTTPError(404, reason="Directory service action is not proxied")
            self._proxy_or_error(
                lambda: _forward_directory_request(
                    "POST",
                    f"/repository/services/{urlparse.quote(name, safe='')}/{action}",
                    body=self.request.body or None,
                    content_type=self.request.headers.get("content-type"),
                )
            )

    class DirectoryServiceProxyHandler(JupyterHandler):
        @web.authenticated
        def get(self, name: str, path: str = "") -> None:
            clean_path = path.strip("/")
            suffix = f"/{urlparse.quote(clean_path, safe='/')}" if clean_path else ""
            self._proxy_or_error(
                lambda: _forward_directory_request(
                    "GET",
                    f"/repository/services/{urlparse.quote(name, safe='')}/proxy{suffix}",
                    query=self.request.query,
                )
            )

    def _proxy_or_error(self: Any, callback: Any) -> None:
        try:
            response = callback()
        except DirectoryProxyError as error:
            self.set_status(error.status_code)
            self.set_header("Content-Type", "application/json")
            self.finish(_json_error(error))
            return
        self._finish_proxy(response)

    def _finish_proxy(self: Any, response: DirectoryProxyResponse) -> None:
        self.set_status(response.status_code)
        self.set_header("Content-Type", response.content_type)
        for key, value in response.headers.items():
            self.set_header(key, value)
        self.finish(response.body)

    for handler in (
        DirectoryEntriesHandler,
        DirectoryFunctionRunHandler,
        DirectoryServiceLifecycleHandler,
        DirectoryServiceProxyHandler,
    ):
        handler._proxy_or_error = _proxy_or_error  # type: ignore[attr-defined]
        handler._finish_proxy = _finish_proxy  # type: ignore[attr-defined]

    base_url = str(serverapp.web_app.settings.get("base_url", "/"))
    prefix = DIRECTORY_API_PREFIX.strip("/")
    handlers = [
        (url_path_join(base_url, prefix, "entries"), DirectoryEntriesHandler),
        (
            url_path_join(base_url, prefix, r"functions/([^/]+)/run"),
            DirectoryFunctionRunHandler,
        ),
        (
            url_path_join(base_url, prefix, r"services/([^/]+)/(start|probe|stop)"),
            DirectoryServiceLifecycleHandler,
        ),
        (
            url_path_join(base_url, prefix, r"services/([^/]+)/proxy/?(.*)"),
            DirectoryServiceProxyHandler,
        ),
    ]
    serverapp.web_app.add_handlers(".*$", handlers)
    serverapp.log.info("Loaded Goblin Directory Jupyter server extension")


def _forward_directory_request(
    method: str,
    upstream_path: str,
    *,
    query: str = "",
    body: bytes | None = None,
    content_type: str | None = None,
    environ: dict[str, str] | None = None,
    opener: Any = urlrequest.urlopen,
    timeout_seconds: float = 120.0,
) -> DirectoryProxyResponse:
    """Forward one signed-in user-server request to the Directory API."""
    environ = environ if environ is not None else os.environ
    token = _hub_user_token(environ)
    base_url = _directory_base_url(environ)
    url = f"{base_url.rstrip('/')}{upstream_path}"
    if query:
        url = f"{url}?{query}"
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
    }
    if body is not None and content_type:
        headers["Content-Type"] = content_type
    request = urlrequest.Request(url, data=body, headers=headers, method=method)
    try:
        with opener(request, timeout=timeout_seconds) as response:
            return DirectoryProxyResponse(
                status_code=response.status,
                body=response.read(),
                content_type=response.headers.get("content-type") or "application/json",
                headers=_response_headers(response.headers.items()),
            )
    except urlerror.HTTPError as error:
        return DirectoryProxyResponse(
            status_code=error.code,
            body=error.read(),
            content_type=(
                error.headers.get("content-type")
                if error.headers and error.headers.get("content-type")
                else "application/json"
            ),
            headers=_response_headers(error.headers.items()) if error.headers else {},
        )
    except OSError as error:
        raise DirectoryProxyError(503, f"Goblin Directory API is unreachable: {error}") from error


def _hub_user_token(environ: dict[str, str]) -> str:
    token = environ.get("JUPYTERHUB_API_TOKEN", "").strip()
    if not token:
        raise DirectoryProxyError(
            503,
            "JUPYTERHUB_API_TOKEN is required in the user notebook server.",
        )
    return token


def _directory_base_url(environ: dict[str, str]) -> str:
    for name in (
        "GOBLIN_KING_DIRECTORY_URL",
        "GOBLIN_KING_REPOSITORY_URL",
        "GOBLIN_KING_API_URL",
    ):
        value = environ.get(name, "").strip().rstrip("/")
        if value:
            return value
    raise DirectoryProxyError(
        503,
        "GOBLIN_KING_DIRECTORY_URL or GOBLIN_KING_API_URL is required in the user server.",
    )


def _response_headers(items: Any) -> dict[str, str]:
    return {
        key: value
        for key, value in dict(items).items()
        if key.lower() not in HOP_BY_HOP_HEADERS
        and key.lower() not in {"content-length", "set-cookie"}
    }


def _json_error(error: DirectoryProxyError) -> bytes:
    return json.dumps({"detail": error.detail}).encode("utf-8")
