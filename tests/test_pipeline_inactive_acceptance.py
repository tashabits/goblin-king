"""Retained terminal evidence must not conceal application activity."""

import copy
import importlib
from pathlib import Path

import pytest


@pytest.fixture
def inactive(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    return importlib.import_module("pipeline_inactive_acceptance").verify_inactive_workloads


def evidence():
    job = {
        "kind": "Job",
        "metadata": {
            "name": "network-evidence",
            "uid": "original-job",
            "labels": {"goblin-king.io/operation": "operation"},
        },
        "status": {"conditions": [{"type": "Complete", "status": "True"}]},
    }
    pod = {
        "kind": "Pod",
        "metadata": {
            "name": "network-evidence-pod",
            "uid": "original-pod",
            "labels": {"goblin-king.io/trust": "network-control"},
            "ownerReferences": [
                {
                    "kind": "Job",
                    "name": "network-evidence",
                    "uid": "original-job",
                    "controller": True,
                }
            ],
        },
        "status": {"phase": "Succeeded"},
    }
    return [job, pod]


@pytest.mark.parametrize("phase,condition", [("Succeeded", "Complete"), ("Failed", "Failed")])
def test_terminal_network_evidence_is_retained(inactive, phase, condition):
    items = evidence()
    items[0]["status"]["conditions"][0]["type"] = condition
    items[1]["status"]["phase"] = phase
    result = inactive(items)
    assert result == [
        {
            "name": "network-evidence-pod",
            "uid": "original-pod",
            "phase": phase,
            "job_name": "network-evidence",
            "job_uid": "original-job",
        }
    ]


@pytest.mark.parametrize("phase", ["Pending", "Running", "Unknown"])
def test_live_network_pod_is_not_ignored(inactive, phase):
    items = evidence()
    items[1]["status"]["phase"] = phase
    with pytest.raises(RuntimeError, match="Pod remains"):
        inactive(items)


def test_completed_application_pod_is_not_network_evidence(inactive):
    items = evidence()
    items[1]["metadata"]["labels"]["app.kubernetes.io/instance"] = "app"
    with pytest.raises(RuntimeError, match="Pod remains"):
        inactive(items)


def test_replaced_or_missing_job_cannot_authorize_retained_pod(inactive):
    items = evidence()
    replacement = copy.deepcopy(items[0])
    replacement["metadata"]["uid"] = "replacement-job"
    for jobs in ([], [replacement]):
        with pytest.raises(RuntimeError, match="Pod remains"):
            inactive(jobs + [items[1]])


def test_active_job_cannot_be_hidden_by_terminal_pod(inactive):
    items = evidence()
    items[0]["status"]["active"] = 1
    with pytest.raises(RuntimeError, match="active Job"):
        inactive(items)


def test_dependent_scenarios_do_not_run_after_failure(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    module = importlib.import_module("pipeline_acceptance")
    proof = module.Acceptance(tmp_path)
    names = ("network-policy", "cleanup")
    proof.state = {"scenarios": {name: {"status": "pending"} for name in names}}
    monkeypatch.setattr(proof, "scenarios", lambda: names)
    monkeypatch.setattr(
        proof,
        "report",
        lambda: all(row["status"] == "passed" for row in proof.state["scenarios"].values()),
    )

    def fail():
        raise RuntimeError("required observation failed")

    def cleanup():
        pytest.fail("dependent cleanup ran after a required failure")

    monkeypatch.setattr(proof, "network_policy", fail)
    monkeypatch.setattr(proof, "cleanup_scenario", cleanup)
    assert proof.run() is False
    assert proof.state["scenarios"]["network-policy"]["status"] == "failed"
    assert proof.state["scenarios"]["cleanup"]["status"] == "pending"
