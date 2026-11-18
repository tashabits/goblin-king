"""Networking checks cannot disappear from the full-suite completion criteria."""

import importlib.util
from pathlib import Path


def test_networking_matrix_is_required_and_cleanup_remains_last(monkeypatch, tmp_path):
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    spec = importlib.util.spec_from_file_location(
        "network_acceptance_test", scripts / "pipeline_acceptance.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    proof = module.Acceptance(tmp_path)
    assert proof.scenarios() == module.SCENARIOS
    proof.state["networking"] = {
        "gateway_class": "fixture",
        "controller_name": "fixture/controller",
    }
    names = proof.scenarios()
    assert len(names) == len(set(names)) == len(module.SCENARIOS) + 21
    assert names[-1] == "cleanup"
    assert names[-3:] == ("side-effect-faults", "network-cleanup", "cleanup")
    assert names.index("network-upgrade") < names.index("side-effect-faults")
    assert "network-tls-lifecycle" in names and "network-backup-restore" in names
    proof.state["scenarios"] = {name: {"status": "passed"} for name in names}
    proof.state["scenarios"]["network-dns-policy"] = {"status": "skipped"}
    import pytest

    with pytest.raises(RuntimeError, match="network-dns-policy"):
        proof.cleanup_scenario()
