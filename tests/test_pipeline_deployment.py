"""Inspection repairs acknowledgement without repeating an already successful Helm effect."""

import json
from unittest.mock import Mock

from goblin_king.pipeline_deployment import HelmRelease


def test_recovery_verifies_completed_upgrade_without_redeploying():
    release = HelmRelease(Mock(), Mock(), Mock(), "test", "app")
    values = {
        "image": "approved",
        "active": True,
        "operationId": "operation",
        "port": 8080,
        "healthPath": "/health",
    }
    release.history = Mock(return_value=[{"revision": 4, "status": "deployed"}])
    release.helm = Mock(return_value=json.dumps(values))
    release.verify = Mock(return_value={"status": 200})
    result = release.recover(values, previous=None)
    assert result["status"] == "success"
    assert result["helm_revision"] == 4
    assert [call.args[0] for call in release.helm.call_args_list] == ["get"]


def test_recovery_verifies_completed_rollback_without_rolling_back_again():
    release = HelmRelease(Mock(), Mock(), Mock(), "test", "app")
    previous = {
        "image": "previous",
        "active": True,
        "port": 8080,
        "health_path": "/health",
        "helm_revision": 3,
    }
    release.history = Mock(return_value=[{"revision": 5, "status": "deployed"}])
    release.helm = Mock(
        return_value=json.dumps({"image": "previous", "active": True, "operationId": "earlier"})
    )
    release.verify = Mock(return_value={"status": 200})
    result = release.recover(
        {"image": "failed", "active": True, "operationId": "new"}, previous=previous
    )
    assert result["status"] == "failed"
    assert result["rollback"]["helm_revision"] == 5
    assert [call.args[0] for call in release.helm.call_args_list] == ["get"]
