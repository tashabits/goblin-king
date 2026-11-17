"""Interruption controls are opt-in, locally scoped and observable."""

import json
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from goblin_king.pipeline_faults import AcceptanceFaultGate, configured_gate
from goblin_king.pipeline_settings import PipelineSettings


def test_disabled_fault_gate_never_contacts_kubernetes():
    core = Mock()
    AcceptanceFaultGate(core, "control")("dispatch.after_submit", "job")
    core.read_namespaced_config_map.assert_not_called()


def test_real_provider_cannot_enable_interruption_controls():
    with pytest.raises(ValueError, match="local simulated"):
        PipelineSettings(acceptance_faults=True)
    with pytest.raises(ValueError, match="local simulated"):
        configured_gate(Mock(), "control", {"acceptance_faults": True})


def test_matching_pause_emits_evidence_and_waits_for_explicit_release(monkeypatch, capsys):
    plan = {"id": "fault-id", "point": "dispatch.after_submit", "identity_prefix": "selected-"}
    core = Mock()
    core.read_namespaced_config_map.side_effect = lambda *_: NS(
        data={"fault.json": json.dumps(plan)}
    )
    gate = AcceptanceFaultGate(core, "control", enabled=True)
    gate("dispatch.after_submit", "other-job")
    assert capsys.readouterr().out == ""
    released = []

    def release(_):
        plan.clear()
        released.append(True)

    monkeypatch.setattr("goblin_king.pipeline_faults.time.sleep", release)
    gate("dispatch.after_submit", "selected-job")
    marker = json.loads(capsys.readouterr().out)["pipeline_fault"]
    assert marker == {
        "id": "fault-id",
        "point": "dispatch.after_submit",
        "identity": "selected-job",
    }
    assert released == [True]
