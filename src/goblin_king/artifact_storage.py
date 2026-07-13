"""Filesystem permissions for artifact storage shared across container identities."""

from __future__ import annotations

import os
import stat
from pathlib import Path

SHARED_ARTIFACT_DIRECTORY_MODE = 0o2770
SHARED_ARTIFACT_FILE_MODE = 0o660


def prepare_shared_artifact_root(root: Path, *, shared_gid: int | None = None) -> None:
    """Create an artifact root and optionally bind it to a restricted workload group."""
    root.mkdir(parents=True, exist_ok=True)
    if shared_gid is None or os.name != "posix":
        return
    current = root.stat()
    if current.st_gid != shared_gid:
        os.chown(root, -1, shared_gid)
        current = root.stat()
    if stat.S_IMODE(current.st_mode) != SHARED_ARTIFACT_DIRECTORY_MODE:
        root.chmod(SHARED_ARTIFACT_DIRECTORY_MODE)
