"""Route availability recovery must never restore broader outbound authority."""

import pytest

from goblin_king.pipeline_network_worker import authorized_route_fallback
from goblin_king.pipeline_networking import NetworkingConfiguration
from tests.test_pipeline_networking import configuration, policy


def payload():
    previous = NetworkingConfiguration.model_validate(configuration()).model_dump(mode="json")
    current = NetworkingConfiguration.model_validate(configuration()).model_dump(mode="json")
    current["ingress"]["path"] = "/broken"
    current["egress"] = {"enabled": False, "hosts": [], "direct": []}
    return {
        "active": True,
        "configuration": current,
        "previous": {"active": True, "configuration": previous},
        "environment": {"namespace": "app-test", "release": "app", "name": "test"},
        "project_id": "fixture",
    }


def test_route_fallback_keeps_revoked_outbound_access_revoked():
    request = payload()
    result = authorized_route_fallback(request, policy())
    assert result["ingress"] == request["previous"]["configuration"]["ingress"]
    assert result["egress"] == request["configuration"]["egress"]
    assert not result["egress"]["enabled"]


@pytest.mark.parametrize(
    "change",
    [
        {"active": False},
        {"previous": None},
        {"probe_only": True},
        {"parent_stage": "network-after"},
    ],
)
def test_inactive_first_install_probe_and_deployment_do_not_restore_a_route(change):
    assert authorized_route_fallback(payload() | change, policy()) is None


def test_disabled_ingress_cannot_be_reexposed_by_fallback():
    request = payload()
    request["configuration"]["ingress"]["enabled"] = False
    assert authorized_route_fallback(request, policy()) is None


def test_old_route_must_still_be_authorized():
    request = payload()
    request["previous"]["configuration"]["ingress"]["hostname"] = "revoked.example.test"
    with pytest.raises(ValueError, match="approved domains"):
        authorized_route_fallback(request, policy())


@pytest.mark.parametrize("failure", ["route", "identity", "configuration", "api"])
def test_only_route_unavailability_can_start_fallback(monkeypatch, failure):
    import io
    import json
    from types import SimpleNamespace

    from kubernetes.client.exceptions import ApiException

    from goblin_king import pipeline_network_worker as module
    from goblin_king.pipeline_kubernetes import RecoveryHold
    from goblin_king.pipeline_network_runtime import RouteUnavailable

    request = payload() | {
        "operation_id": "operation",
        "policy_revision": policy().revision,
        "port": 8080,
        "health_path": "/health",
    }
    installed = {"networking": policy().model_dump(mode="json"), "api_url": "http://control"}
    core = SimpleNamespace(
        read_namespaced_config_map=lambda *args: SimpleNamespace(
            data={"config.json": json.dumps(installed)}
        )
    )
    monkeypatch.setattr(module, "kubernetes_clients", lambda: (None, core))
    monkeypatch.setenv("GOBLIN_CONTROL_NAMESPACE", "control")
    monkeypatch.setattr(module, "Path", lambda path: SimpleNamespace(read_text=lambda: "token"))
    monkeypatch.setattr(module, "urlopen", lambda *a, **k: io.BytesIO(json.dumps(request).encode()))
    monkeypatch.setattr(module, "configured_gate", lambda *args: None)
    monkeypatch.setattr(module.client, "ApiClient", lambda: None)
    ledgers = []

    def ledger(*args, **kwargs):
        result = SimpleNamespace(
            name="ledger", document={"result": None}, save=lambda: None, finish=lambda value: None
        )
        ledgers.append(result)
        return result

    error = {
        "route": RouteUnavailable("route unavailable"),
        "identity": RecoveryHold("identity changed"),
        "configuration": ValueError("configuration invalid"),
        "api": ApiException(status=409),
    }[failure]

    def execute(*args, **kwargs):
        if len(ledgers) == 1:
            raise error
        return {"healthy": True}

    monkeypatch.setattr(module, "RetainedExecution", ledger)
    monkeypatch.setattr(
        module, "NetworkRuntime", lambda *a, **k: SimpleNamespace(configure=execute)
    )
    if failure == "route":
        result = module.configure(request)
        assert result.data["route_rollback"] is True
        assert len(ledgers) == 2
    else:
        with pytest.raises(type(error)):
            module.configure(request)
        assert len(ledgers) == 1
        assert "route_fallback" not in ledgers[0].document
