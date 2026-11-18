"""Network effect acknowledgement, identity fencing and drift protection."""

import copy
from types import SimpleNamespace

import pytest
from kubernetes.client.exceptions import ApiException

from goblin_king.pipeline_kubernetes import RecoveryHold
from goblin_king.pipeline_network_runtime import NetworkRuntime
from tests.test_pipeline_networking import policy


class API:
    def __init__(self):
        self.resources = {}
        self.writes = 0

    def call_api(self, path, method, **kwargs):
        body = copy.deepcopy(kwargs.get("body"))
        if method == "GET":
            if path not in self.resources:
                raise ApiException(status=404)
            return copy.deepcopy(self.resources[path])
        self.writes += 1
        if method == "DELETE":
            assert body["preconditions"]["uid"] == self.resources[path]["metadata"]["uid"]
            del self.resources[path]
            return {}
        if method == "POST":
            path += "/" + body["metadata"]["name"]
        metadata = body["metadata"]
        metadata.update(
            uid=self.resources.get(path, {}).get("metadata", {}).get("uid", "original"),
            resourceVersion=str(self.writes),
            generation=self.writes,
        )
        self.resources[path] = body
        return copy.deepcopy(body)


def resource():
    return {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "namespace": "app-test",
            "name": "app-egress",
            "labels": {"app.kubernetes.io/instance": "app", "goblin-king.io/networking": "true"},
            "annotations": {"goblin-king.io/operation": "one"},
        },
        "data": {"squid.conf": "http_access deny all"},
    }


def runtime():
    api = API()
    ledger = SimpleNamespace(document={}, save=lambda: None, fault_hook=lambda *args: None)
    return NetworkRuntime(
        api,
        ledger,
        policy(),
        namespace="app-test",
        release="app",
        project_id="fixture",
        environment="test",
        timeout=1,
    )


def test_response_lost_after_external_update_is_observed_without_second_write():
    current = runtime()

    def crash(point, key):
        if point == "networking.after_apply":
            raise RuntimeError("interruption")

    current.ledger.fault_hook = crash
    with pytest.raises(RuntimeError):
        current.apply(resource())
    assert current.api.writes == 1
    current.ledger.fault_hook = lambda *args: None
    observed = current.apply(resource())
    assert observed["metadata"]["uid"] == "original"
    assert current.api.writes == 1


def test_kubernetes_omitted_empty_network_rules_preserve_deny_semantics():
    current = runtime()
    current.api.resources[
        "/apis/networking.k8s.io/v1/namespaces/app-test/networkpolicies/app-networking"
    ] = {"spec": {"policyTypes": ["Ingress", "Egress"], "podSelector": {}}}
    observed = current.read("NetworkPolicy", "app-networking")
    assert observed["spec"]["ingress"] == observed["spec"]["egress"] == []
    assert current.api.writes == 0


def test_additional_allow_policy_cannot_hide_behind_an_ownership_label():
    current = runtime()
    current.api.resources["/apis/networking.k8s.io/v1/namespaces/app-test/networkpolicies"] = {
        "items": [
            {
                "metadata": {
                    "name": "app-extra",
                    "labels": {
                        "app.kubernetes.io/instance": "app",
                        "goblin-king.io/networking": "true",
                    },
                },
                "spec": {"podSelector": {}, "egress": [{}]},
            }
        ]
    }
    with pytest.raises(RecoveryHold, match="additional outbound policy"):
        current.inspect_policy_boundary()


@pytest.mark.parametrize("drift", ["identity", "configuration"])
def test_acknowledged_resource_drift_holds_instead_of_overwriting(drift):
    current = runtime()
    current.apply(resource())
    stored = next(iter(current.api.resources.values()))
    if drift == "identity":
        stored["metadata"]["uid"] = "replacement"
    else:
        stored["data"]["squid.conf"] = "http_access allow all"
    with pytest.raises(RecoveryHold):
        current.apply(resource())
    assert current.api.writes == 1


def test_conflicting_intent_cannot_reuse_effect_identity():
    current = runtime()
    current.apply(resource())
    changed = resource()
    changed["data"]["squid.conf"] = "different config"
    with pytest.raises(RecoveryHold, match="intent"):
        current.apply(changed)


def test_foreign_resource_cannot_be_adopted_or_removed():
    current = runtime()
    current.apply(resource())
    stored = next(iter(current.api.resources.values()))
    stored["metadata"]["labels"]["app.kubernetes.io/instance"] = "other"
    current.ledger.document = {}
    with pytest.raises(RecoveryHold, match="owner"):
        current.apply(resource())
    with pytest.raises(RecoveryHold, match="owner"):
        current.remove("ConfigMap", "app-egress")
    assert current.api.writes == 1


def test_completed_deletion_cannot_delete_a_replacement():
    current = runtime()
    current.apply(resource())
    original = copy.deepcopy(current.api.resources)
    current.remove("ConfigMap", "app-egress")
    assert not current.api.resources
    current.api.resources = original
    with pytest.raises(RecoveryHold, match="reappeared"):
        current.remove("ConfigMap", "app-egress")
    assert current.api.writes == 2


def test_new_kubernetes_client_uses_status_response_map():
    class CurrentAPI(API):
        def call_api(self, path, method, *, response_types_map, **kwargs):
            assert response_types_map == {200: "object", 201: "object", 202: "object"}
            assert "response_type" not in kwargs
            return super().call_api(path, method, **kwargs)

    prior = runtime()
    current = NetworkRuntime(
        CurrentAPI(),
        prior.ledger,
        prior.policy,
        namespace="app-test",
        release="app",
        project_id="fixture",
        environment="test",
    )
    assert current.apply(resource())["metadata"]["uid"] == "original"
