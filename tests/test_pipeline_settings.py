"""Reject onboarding that differs from exact installed authority bounds."""

import copy

import pytest

from goblin_king.pipeline_settings import verify_installed_routes
from tests.test_pipeline_store import registration


def test_onboarding_rejects_uninstalled_destinations():
    reg = registration()
    config = {
        "sources": {
            reg.source_repository: {
                "namespace": "builds",
                "node": "node-a",
                "github_secret": "app",
                "publisher_secret": "publish",
                "pull_secret": "read",
                "staging_repository": "staging",
                "image_repositories": [route.image_repository for route in reg.environments],
            }
        },
        "releases": {
            route.namespace + "/" + route.release: {
                "environment": route.name,
                "pull_secret": "read",
            }
            for route in reg.environments
        },
    }
    verify_installed_routes(reg, config)
    wrong = copy.deepcopy(config)
    wrong["sources"][reg.source_repository]["image_repositories"] = []
    with pytest.raises(ValueError, match="registry destination"):
        verify_installed_routes(reg, wrong)
    wrong = copy.deepcopy(config)
    wrong["releases"] = {}
    with pytest.raises(ValueError, match="deployment configuration"):
        verify_installed_routes(reg, wrong)
    wrong = copy.deepcopy(config)
    wrong["sources"][reg.source_repository]["namespace"] = reg.environments[0].namespace
    with pytest.raises(ValueError, match="separate namespaces"):
        verify_installed_routes(reg, wrong)


def test_configuration_replay_does_not_depend_on_provider_availability(tmp_path):
    from goblin_king.pipeline_service import PipelineService
    from goblin_king.pipeline_settings import PipelineSettings
    from tests.test_pipeline_state import attached

    state, registration, key = attached(tmp_path)
    service = PipelineService(state.store, PipelineSettings(), provider=object())
    replay = service.attach(registration, 1, 0, "operator", "attach")
    assert replay["key"] == key and replay["version"] == 1
    enabled = state.management(
        key,
        expected_version=1,
        enabled=True,
        heads={route.branch: "a" * 40 for route in registration.environments},
        actor="operator",
        idempotency_key="enable",
    )
    assert service.management(key, 1, True, "operator", "enable") == enabled


def test_execution_acknowledgement_retries_without_losing_durable_result(tmp_path):
    from goblin_king.pipeline_service import PipelineService
    from goblin_king.pipeline_settings import PipelineSettings
    from goblin_king.store_schema import pipeline_builds_table
    from tests.test_pipeline_state import admit, enabled

    state, _, key = enabled(tmp_path)
    admission = admit(state, key)
    result = {"status": "failed", "data": {}, "error": "application failure"}
    state.finish_build(admission.request.build_id, result, job_id="stable-job", run_id="run")
    service = PipelineService(state.store, PipelineSettings(), provider=object())
    calls = []

    def acknowledge(job_id, actual):
        calls.append(job_id)
        assert actual == result
        if len(calls) == 1:
            raise OSError("temporary acknowledgement outage")

    service.execution_acknowledger = acknowledge
    service.acknowledge_executions()
    with state.store.engine.connect() as connection:
        row = state.row(connection, pipeline_builds_table, admission.request.build_id)
        assert row["document"]["result"] == result
        assert row["document"]["acknowledgement_error"]
    service.acknowledge_executions()
    service.acknowledge_executions()
    assert calls == ["stable-job", "stable-job"]
    with state.store.engine.connect() as connection:
        assert state.row(connection, pipeline_builds_table, admission.request.build_id)["document"][
            "execution_acknowledged"
        ]
