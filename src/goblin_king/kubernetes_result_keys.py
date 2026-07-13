"""Redis key ownership for Kubernetes worker and retained-forwarder results."""

WORKER_RESULT_KEY_PREFIX = "goblin-king:results:"
FORWARDED_RESULT_KEY_PREFIX = "goblin-king:forwarded-results:"


def worker_result_key(run_id: str) -> str:
    """Return the established worker-owned result key."""
    return f"{WORKER_RESULT_KEY_PREFIX}{run_id}"


def forwarded_result_key(run_id: str) -> str:
    """Return the forwarder-owned key proving retention processing completed."""
    return f"{FORWARDED_RESULT_KEY_PREFIX}{run_id}"
