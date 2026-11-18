"""Rejected release claims retain bounded, actionable API reasons."""

import io
import json
from types import SimpleNamespace as NS
from unittest.mock import Mock
from urllib.error import HTTPError

import pytest

from goblin_king import pipeline_deploy_worker
from goblin_king.pipeline_kubernetes import RecoveryHold


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (
            b'{"detail":"execution Job identity has not been acknowledged"}',
            "execution Job identity has not been acknowledged",
        ),
        (b"not JSON", "HTTP 409"),
        (json.dumps({"detail": "x" * 500}).encode(), "x" * 240),
    ],
)
def test_rejected_claim_reports_bounded_reason_without_deploying(monkeypatch, response, expected):
    core = Mock()
    core.read_namespaced_config_map.return_value = NS(
        data={
            "config.json": json.dumps(
                {
                    "releases": {"test/app": {"environment": "test"}},
                    "api_url": "http://api",
                }
            )
        }
    )
    monkeypatch.setattr(pipeline_deploy_worker, "kubernetes_clients", lambda: (Mock(), core))
    monkeypatch.setattr(pipeline_deploy_worker, "current_kubernetes_namespace", lambda: "control")
    monkeypatch.setattr(pipeline_deploy_worker, "configured_gate", lambda *args: Mock())
    monkeypatch.setattr(pipeline_deploy_worker, "Path", lambda path: NS(read_text=lambda: "token"))
    request = Mock(
        side_effect=HTTPError("http://api/claim", 409, "Conflict", {}, io.BytesIO(response))
    )
    monkeypatch.setattr(pipeline_deploy_worker, "urlopen", request)
    helm = Mock()
    monkeypatch.setattr(pipeline_deploy_worker, "HelmRelease", helm)
    payload = {
        "operation_id": "operation",
        "environment": {
            "namespace": "test",
            "release": "app",
            "name": "test",
        },
    }
    with pytest.raises(RecoveryHold) as error:
        pipeline_deploy_worker.deploy(payload)
    assert expected in str(error.value)
    assert len(str(error.value)) <= 280
    request.assert_called_once()
    helm.assert_not_called()
