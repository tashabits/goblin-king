"""Read-only verification of restored networking before delivery is enabled."""

from types import SimpleNamespace

from kubernetes import client

from goblin_king.pipeline_kubernetes import RecoveryHold
from goblin_king.pipeline_network_runtime import NetworkRuntime
from goblin_king.pipeline_networking import NetworkingPolicy


class ReadOnlyAPI:
    def __init__(self, api):
        self.api = api

    def call_api(self, path, method, *, response_types_map, **kwargs):
        if method != "GET":
            raise RecoveryHold("restoration verification cannot mutate cluster resources")
        return self.api.call_api(path, method, response_types_map=response_types_map, **kwargs)


def verify_network_recovery(policy, release, network):
    policy = NetworkingPolicy.model_validate(policy)
    observed = network.get("observed")
    if not observed:
        if network["network_version"]:
            raise RecoveryHold("restored networking has no verified observation")
        return None
    document = release["document"]
    target = document["environment"]
    if (
        document["operation_id"]
        or network["operation_id"]
        or network["hold"]
        or observed["policy_revision"] != policy.revision
        or observed["configuration"] != network["desired"]
        or observed["active"] != bool(document["active"] and document["observed"])
    ):
        raise RecoveryHold("restored networking requires explicit reconciliation")
    policy.validate_configuration(
        observed["configuration"],
        identity=target["namespace"] + "/" + target["release"],
        project_id=release["project_id"],
        environment=target["name"],
    )
    runtime = NetworkRuntime(
        ReadOnlyAPI(client.ApiClient()),
        SimpleNamespace(document={}, save=lambda: None, fault_hook=lambda *_: None),
        policy,
        namespace=target["namespace"],
        release=target["release"],
        project_id=release["project_id"],
        environment=target["name"],
        timeout=60,
    )
    app = document["observed"] or {}
    return runtime.configure(
        observed["configuration"],
        operation_id="restore-verification",
        active=observed["active"],
        port=app.get("port", 8080),
        health_path=app.get("health_path", "/health"),
        previous=observed,
        probe_only=True,
        expected_image=app.get("image"),
    )
