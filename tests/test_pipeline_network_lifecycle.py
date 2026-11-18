"""Release ordering, inactive delivery, rollback and nested termination boundaries."""

import io
import json
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from goblin_king import pipeline_deploy_worker as implementation
from goblin_king.pipeline_deployment import HelmRelease
from goblin_king.pipeline_identity import terminated_execution
from goblin_king.pipeline_kubernetes import RecoveryHold
from goblin_king.pipeline_state import PipelineConflict
from tests.test_pipeline_identity import evidence


def deployment(monkeypatch, *, active=True):
    events = []
    payload = {
        "operation_id": "operation",
        "environment": {
            "namespace": "test",
            "release": "app",
            "name": "test",
        },
        "values": {"active": active},
        "previous": {"active": True},
        "networking": {"previous": {"active": True}},
    }
    core = Mock()
    core.read_namespaced_config_map.return_value = NS(
        data={
            "config.json": json.dumps(
                {
                    "releases": {
                        "test/app": {
                            "environment": "test",
                            "pull_secret": "pull",
                            "persistence": {},
                        }
                    },
                    "api_url": "http://api",
                    "revision": "configured",
                }
            )
        }
    )
    monkeypatch.setattr(implementation, "kubernetes_clients", lambda: (Mock(), core))
    monkeypatch.setattr(implementation, "current_kubernetes_namespace", lambda: "control")
    monkeypatch.setattr(implementation, "configured_gate", lambda *args: Mock())
    monkeypatch.setattr(implementation, "Path", lambda _: NS(read_text=lambda: "bound-token"))
    monkeypatch.setattr(
        implementation, "urlopen", lambda *a, **k: io.BytesIO(json.dumps(payload).encode())
    )
    ledger = NS(
        document={"result": None}, save=lambda: None, finish=lambda _: events.append("finish")
    )
    monkeypatch.setattr(implementation, "RetainedExecution", lambda *a, **k: ledger)
    helm = Mock()

    def apply(*args, **kwargs):
        events.append("helm")
        return {"status": "success", "active": active}

    helm.apply.side_effect = apply

    def rollback(*args, **kwargs):
        events.append("rollback")
        return {"status": "failed", "rollback": {"active": True}}

    helm.rollback.side_effect = rollback
    monkeypatch.setattr(implementation, "HelmRelease", lambda *a, **k: helm)

    def phase(*args, **kwargs):
        events.append((kwargs["stage"], kwargs["active"]))
        return {"active": kwargs["active"]}

    monkeypatch.setattr(implementation, "network_phase", phase)
    return payload, events, helm, phase


@pytest.mark.parametrize("active", [True, False])
def test_networking_brackets_helm_and_retains_activation(monkeypatch, active):
    payload, events, _, _ = deployment(monkeypatch, active=active)
    result = implementation.deploy(payload)
    assert events == [("network-before", active), "helm", ("network-after", active), "finish"]
    assert result.data["release"]["networking"]["active"] is active


def test_failed_external_route_restores_previous_release_before_recording(monkeypatch):
    payload, events, helm, original = deployment(monkeypatch)

    def phase(*args, **kwargs):
        if kwargs["stage"] == "network-after":
            events.append("route-failed")
            raise RecoveryHold("route did not pass")
        return original(*args, **kwargs)

    monkeypatch.setattr(implementation, "network_phase", phase)
    result = implementation.deploy(payload)
    assert events == [
        ("network-before", True),
        "helm",
        "route-failed",
        "rollback",
        ("network-rollback", True),
        "finish",
    ]
    helm.rollback.assert_called_once()
    assert result.data["release"]["rollback"]["networking"]["active"] is True


def test_first_install_route_failure_does_not_invent_rollback(monkeypatch):
    payload, _, helm, original = deployment(monkeypatch)
    payload["previous"] = None

    def phase(*args, **kwargs):
        if kwargs["stage"] == "network-after":
            raise RecoveryHold("route did not pass")
        return original(*args, **kwargs)

    monkeypatch.setattr(implementation, "network_phase", phase)
    with pytest.raises(RecoveryHold):
        implementation.deploy(payload)
    helm.rollback.assert_not_called()


def test_parent_termination_does_not_allow_live_nested_job():
    core, batch, pod = evidence()
    pod.spec.init_containers = []
    job = NS(metadata=NS(name="child", uid="job-uid"), status=NS(succeeded=0, failed=0))
    batch.list_namespaced_job.return_value = NS(items=[job])
    operation = {
        "document": {
            "job_id": "parent",
            "execution": {
                "networking": {"image": "approved"},
                "operation_id": "operation",
                "environment": {"namespace": "app-test"},
            },
        }
    }
    with pytest.raises(PipelineConflict, match="nested networking Job has not terminated"):
        terminated_execution(operation, core, batch, "control")
    job.status.succeeded = 1
    assert terminated_execution(operation, core, batch, "control")["network_job_uids"] == [
        "job-uid"
    ]


def test_application_health_does_not_require_component_label_on_deployment():
    core, apps, batch = Mock(), Mock(), Mock()
    apps.list_namespaced_deployment.return_value = NS(
        items=[
            NS(metadata=NS(name="app"), spec=NS(replicas=0)),
            NS(metadata=NS(name="app-egress"), spec=NS(replicas=1)),
        ]
    )
    core.list_namespaced_pod.return_value = NS(
        items=[NS(metadata=NS(labels={"goblin-king.io/networking": "true"}))]
    )
    batch.list_namespaced_cron_job.return_value = NS(items=[NS(spec=NS(suspend=True))])
    release = HelmRelease(core, apps, batch, "test", "app", timeout=0)
    assert release.verify(image="approved", active=False, port=8080, health_path="/health")[
        "workloads_stopped"
    ]
    assert (
        apps.list_namespaced_deployment.call_args.kwargs["label_selector"]
        == "app.kubernetes.io/instance=app"
    )
