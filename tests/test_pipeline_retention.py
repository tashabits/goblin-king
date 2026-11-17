"""Retention must never discard unsettled or unacknowledged execution evidence."""

import copy
from datetime import UTC, datetime

import pytest

from scripts.pipeline_retention import eligible


def records():
    result = {"status": "success", "data": {"digest": "sha256:example"}}
    return (
        {
            "status": "published",
            "document": {"job_id": "job", "result": result, "execution_acknowledged": True},
        },
        {
            "identity": {"job_id": "job"},
            "result": copy.deepcopy(result),
            "acknowledged": True,
            "acknowledged_at": "2026-11-01T00:00:00+00:00",
        },
    )


def test_only_old_matching_acknowledged_result_is_eligible():
    row, ledger = records()
    assert eligible(row, ledger, datetime(2026, 11, 17, tzinfo=UTC), 168)
    assert not eligible(row, ledger, datetime(2026, 11, 1, 0, 30, tzinfo=UTC), 1)
    ledger["result"]["data"]["digest"] = "changed"
    assert not eligible(row, ledger, datetime(2026, 11, 17, tzinfo=UTC), 168)


@pytest.mark.parametrize("status", ["held", "running", "admitted", "recording"])
def test_unsettled_status_is_never_eligible(status):
    row, ledger = records()
    row["status"] = status
    assert not eligible(row, ledger, datetime(2026, 11, 17, tzinfo=UTC), 168)


@pytest.mark.parametrize("side", ["database", "execution"])
def test_both_acknowledgements_are_required(side):
    row, ledger = records()
    if side == "database":
        row["document"]["execution_acknowledged"] = False
    else:
        ledger["acknowledged"] = False
    assert not eligible(row, ledger, datetime(2026, 11, 17, tzinfo=UTC), 168)
