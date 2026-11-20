"""Unscheduled outage Pods do not count toward verified node coverage."""

import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("nodes", [("one", "two"), ("one", "one")])
def test_node_loss_requires_two_assigned_nodes_despite_pending_pods(tmp_path, monkeypatch, nodes):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    module = importlib.import_module("pipeline_network_storage_acceptance")
    saved = []
    network = SimpleNamespace(
        output=tmp_path,
        delivery=SimpleNamespace(prefix="fixture"),
        proof=None,
        save=lambda name, evidence: saved.append((name, evidence)),
    )
    verifier = module.NetworkStorageAcceptance(network)
    evidence = {}
    for phase, node in zip(
        ("before", "during", "after"), (nodes[0], nodes[1], nodes[1]), strict=True
    ):
        evidence[phase] = {
            "application_pods": [
                {"spec": {"nodeName": node}, "status": {"phase": "Running"}},
                {"spec": {}, "status": {"phase": "Pending"}},
            ],
            "proxy_pods": [
                {
                    "metadata": {"namespace": "fixture-test"},
                    "spec": {"nodeName": node},
                    "status": {"phase": "Running"},
                }
            ],
            "connection": {"direct": "denied"},
        }
    verifier.path.write_text(json.dumps(evidence))
    if len(set(nodes)) == 2:
        verifier.node_loss()
        assert saved == [("network-node-loss", evidence)]
    else:
        with pytest.raises(RuntimeError, match="across both nodes"):
            verifier.node_loss()
        assert not saved
