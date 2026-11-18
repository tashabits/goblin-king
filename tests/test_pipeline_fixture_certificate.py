"""Fixture HTTPS trust is public source data tied to the exact reviewed local commit."""

import importlib.util
from pathlib import Path

import pytest
from fastapi import HTTPException

from examples.pipeline.provider_simulator import Simulator


def test_public_fixture_certificate_is_committed_after_repair(tmp_path):
    path = Path(__file__).resolve().parents[1] / "scripts/pipeline_networking_prepare.py"
    spec = importlib.util.spec_from_file_location("network_cert_fixture", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    cert, key = module.certificate(["allowed.fixture.example.test"])
    simulator = Simulator(tmp_path)
    _, directory = simulator.repository("fixture", "python-api")
    before = simulator.head(directory, "test")
    with pytest.raises(HTTPException) as error:
        simulator.merge({"repository": "fixture/python-api", "public_ca": key.decode()})
    assert error.value.status_code == 422
    assert simulator.head(directory, "test") == before
    delivery = simulator.merge(
        {"repository": "fixture/python-api", "fault": "repair", "public_ca": cert.decode()}
    )
    commit = delivery["event"]["after"]
    assert simulator.git(directory, "show", commit + ":fixture-ca.pem") == cert.decode().strip()
    assert "PRIVATE" not in simulator.git(directory, "show", commit + ":fixture-ca.pem")
