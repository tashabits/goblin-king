"""Receipt replacement survives transient locks without discarding the previous state."""

import importlib
import json
from pathlib import Path

import pytest


@pytest.mark.parametrize("persistent", [False, True])
def test_save_preserves_receipts_during_file_lock(monkeypatch, tmp_path, persistent):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    module = importlib.import_module("pipeline_acceptance")
    proof = module.Acceptance(tmp_path)
    proof.state = {"generation": 1}
    proof.save()
    proof.state = {"generation": 2}
    original = Path.replace
    attempts = []
    clock = [0]

    def replace(source, destination):
        attempts.append(source)
        assert json.loads(destination.read_text()) == {"generation": 1}
        if persistent or len(attempts) == 1:
            raise PermissionError("receipt is open")
        return original(source, destination)

    def sleep(seconds):
        clock[0] += 1

    monkeypatch.setattr(Path, "replace", replace)
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(module.time, "sleep", sleep)
    if persistent:
        with pytest.raises(PermissionError, match="receipt is open"):
            proof.save()
        assert json.loads(proof.state_path.read_text()) == {"generation": 1}
        assert json.loads(proof.state_path.with_suffix(".tmp").read_text()) == proof.state
        assert len(attempts) == 6
    else:
        proof.save()
        assert json.loads(proof.state_path.read_text()) == proof.state
        assert not proof.state_path.with_suffix(".tmp").exists()
        assert len(attempts) == 2
