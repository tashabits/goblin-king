"""A lost admission response must replay the exact reviewed request."""

import copy
import importlib
from pathlib import Path

import pytest


def fixture(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    module = importlib.import_module("pipeline_network_acceptance")

    class Delivery:
        version = 4
        previews = 0
        admitted = []

        def release(self, *_):
            return {"key": "release", "version": self.version}

        def request(self, path, body):
            if path == "/pipeline/networking/preview":
                assert set(body) == {"release_key", "configuration"}
                self.previews += 1
                return {"preview_id": "preview-4", "expected_version": self.version}
            self.admitted.append(copy.deepcopy(body))
            if len(self.admitted) == 1:
                self.version += 1
                raise ConnectionError("response lost after admission")
            assert body == self.admitted[0]
            return {"operation_id": "same-operation"}

    network = object.__new__(module.NetworkAcceptance)
    network.output, network.delivery = tmp_path, Delivery()
    return network


def test_response_loss_replays_original_preview_and_expected_version(monkeypatch, tmp_path):
    network = fixture(monkeypatch, tmp_path)
    configuration = {"ingress": {"enabled": False}, "egress": {"enabled": False}}
    with pytest.raises(ConnectionError):
        network.begin("python", "test", configuration, "one-action")
    result = network.begin("python", "test", configuration, "one-action")
    assert result["operation_id"] == "same-operation"
    assert network.delivery.previews == 1
    assert len(network.delivery.admitted) == 2
    assert network.delivery.admitted[1]["expected_version"] == 4


def test_checkpoint_rejects_changed_configuration_before_admission(monkeypatch, tmp_path):
    network = fixture(monkeypatch, tmp_path)
    with pytest.raises(ConnectionError):
        network.begin("python", "test", {"egress": {"enabled": False}}, "one-action")
    with pytest.raises(RuntimeError, match="identity conflicts"):
        network.begin("python", "test", {"egress": {"enabled": True}}, "one-action")
    assert len(network.delivery.admitted) == network.delivery.previews == 1
