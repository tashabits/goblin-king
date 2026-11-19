"""Rollout failures are independent of source-test failures in both local fixtures."""

import pytest

from examples.pipeline.provider_simulator import Simulator


@pytest.mark.parametrize("implementation", ["python-api", "node-api"])
def test_health_fault_changes_only_runtime_configuration_and_repair_restores_it(
    tmp_path, implementation
):
    simulator = Simulator(tmp_path)
    _, directory = simulator.repository("fixture", implementation)
    before = simulator.head(directory, "test")
    original = (directory / "Dockerfile").read_text()
    delivery = simulator.merge(
        {"repository": f"fixture/{implementation}", "fault": "health", "webhook": False}
    )
    commit = delivery["event"]["after"]
    changed = simulator.git(directory, "diff", "--name-only", before, commit).splitlines()
    assert set(changed) == {"Dockerfile", "VERSION"}
    dockerfile = (directory / "Dockerfile").read_text()
    test_stages, runtime = dockerfile.rsplit("FROM base AS runtime", 1)
    assert "FAIL_HEALTH" not in test_stages
    assert "ENV FAIL_HEALTH=1" in runtime
    simulator.merge(
        {"repository": f"fixture/{implementation}", "fault": "repair", "webhook": False}
    )
    assert (directory / "Dockerfile").read_text() == original
