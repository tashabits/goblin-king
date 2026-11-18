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
