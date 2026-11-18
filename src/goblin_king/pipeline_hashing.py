"""Canonical pipeline fingerprints without persistence dependencies."""

import hashlib
import json
from typing import Any


def fingerprint(value: Any) -> str:
    """Hash unambiguous canonical JSON identities."""
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
