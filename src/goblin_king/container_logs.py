"""Shared bounded container-log event payload helpers."""

from __future__ import annotations

from typing import Any, Literal

from goblin_king.resource_policies import ResourcePolicy

DEFAULT_RUNTIME_LOG_CAPTURE_BYTES = 64 * 1024


def container_log_payload(
    *,
    kind: str,
    image: str,
    container_name: str,
    stdout: str | bytes | None,
    stderr: str | bytes | None,
    exit_code: int | None,
    timed_out: bool,
    max_bytes: int,
    stream_mode: Literal["combined"] | None = None,
    stdout_was_truncated: bool = False,
    stderr_was_truncated: bool = False,
    stdout_observed_bytes: int | None = None,
    stderr_observed_bytes: int | None = None,
) -> dict[str, Any]:
    """Build the stable bounded payload shared by container runtimes."""
    if stream_mode == "combined":
        stdout_limit = max_bytes
        stderr_limit = 0
    else:
        stdout_limit = (max_bytes + 1) // 2
        stderr_limit = max_bytes // 2
    stdout_tail, stdout_truncated, stdout_bytes = tail_text(stdout, stdout_limit)
    stderr_tail, stderr_truncated, stderr_bytes = tail_text(stderr, stderr_limit)
    stdout_truncated = stdout_truncated or stdout_was_truncated
    stderr_truncated = stderr_truncated or stderr_was_truncated
    payload: dict[str, Any] = {
        "kind": kind,
        "image": image,
        "container_name": container_name,
        "exit_code": exit_code,
        "timed_out": timed_out,
        "stdout": stdout_tail,
        "stderr": stderr_tail,
        "stdout_truncated": stdout_truncated,
        "stderr_truncated": stderr_truncated,
        "truncated": stdout_truncated or stderr_truncated,
        "max_bytes": max_bytes,
        "stdout_bytes": (stdout_bytes if stdout_observed_bytes is None else stdout_observed_bytes),
        "stderr_bytes": (stderr_bytes if stderr_observed_bytes is None else stderr_observed_bytes),
    }
    if stream_mode is not None:
        payload["stream_mode"] = stream_mode
    return payload


def log_capture_limit(resource_policy: ResourcePolicy | None) -> int:
    """Resolve the effective retained-log byte ceiling."""
    if resource_policy is not None and resource_policy.logs.max_bytes is not None:
        return resource_policy.logs.max_bytes
    return DEFAULT_RUNTIME_LOG_CAPTURE_BYTES


def tail_text(value: str | bytes | None, max_bytes: int) -> tuple[str, bool, int]:
    """Return a UTF-8-safe best-effort tail and its observed byte count."""
    if value is None:
        return "", False, 0
    text = value.decode("utf-8", errors="replace") if isinstance(value, bytes) else str(value)
    encoded = text.encode("utf-8")
    byte_count = len(encoded)
    if max_bytes <= 0:
        return "", byte_count > 0, byte_count
    if byte_count <= max_bytes:
        return text, False, byte_count
    return encoded[-max_bytes:].decode("utf-8", errors="replace"), True, byte_count
