"""Repository onboarding contracts reject ambiguous routes and privileged source fields."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from goblin_king.pipeline_contracts import (
    PipelineCatalog,
    PipelineRegistration,
    PipelineServiceContract,
    SourceChange,
    validate_service_checkout,
)


def registration_data(**overrides):
    """Return a generic operator-owned registration with isolated release destinations."""
    data = {
        "service_id": "sample-api",
        "project_id": "sample-project",
        "source_repository": "Example/Sample-Api",
        "pipeline_revision": "a" * 40,
        "catalog_revision": "b" * 40,
        "environments": [
            {
                "name": "test",
                "branch": "test",
                "target": "local-cluster",
                "namespace": "sample-test",
                "release": "sample-api-test",
                "deployment_profile": "http-service",
                "image_repository": "registry.example/sample-test/app",
                "active": False,
            },
            {
                "name": "prod",
                "branch": "prod",
                "target": "local-cluster",
                "namespace": "sample-prod",
                "release": "sample-api",
                "deployment_profile": "http-service",
                "image_repository": "registry.example/sample-prod/app",
            },
        ],
    }
    return data | overrides


def source_change(**overrides):
    """Return a normalized change from a future authenticated Git adapter."""
    return SourceChange.model_validate(
        {
            "delivery_id": "delivery-1",
            "repository": "example/sample-api",
            "branch": "test",
            "commit": "c" * 40,
            "actor": "maintainer",
        }
        | overrides
    )


def test_source_contract_has_portable_defaults_and_no_deployment_authority():
    contract = PipelineServiceContract(port=8080)
    assert contract.context == "."
    assert contract.dockerfile == "Dockerfile"
    assert contract.test_command == ("sh", "ci/test.sh")
    assert contract.health_path == "/health"
    with pytest.raises(ValidationError):
        PipelineServiceContract(port=8080, service_account="cluster-admin")


@pytest.mark.parametrize("path", ["../app", "/app", "C:/app", "a/../b", "a\\b", "", "a\n"])
@pytest.mark.parametrize("field", ["context", "dockerfile"])
def test_source_contract_rejects_escaping_or_platform_specific_paths(field, path):
    with pytest.raises(ValidationError):
        PipelineServiceContract.model_validate({"port": 8080, field: path})


@pytest.mark.parametrize("command", [[], "sh ci/test.sh", [""], ["sh", "bad\narg"]])
def test_source_contract_requires_nonempty_argument_vector(command):
    with pytest.raises(ValidationError):
        PipelineServiceContract(port=8080, test_command=command)


def test_checkout_validation_resolves_the_actual_context_and_dockerfile(tmp_path):
    (tmp_path / "service").mkdir()
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("FROM scratch\n", encoding="utf-8")
    paths = validate_service_checkout(
        PipelineServiceContract(port=8080, context="service"), tmp_path
    )
    assert paths.context == (tmp_path / "service").resolve()
    assert paths.dockerfile == dockerfile.resolve()


def test_checkout_validation_rejects_missing_files(tmp_path):
    with pytest.raises(ValueError, match="Dockerfile"):
        validate_service_checkout(PipelineServiceContract(port=8080), tmp_path)


def test_checkout_validation_rejects_resolved_path_escape(tmp_path, monkeypatch):
    """Exercise symlink resolution without requiring Windows symlink privileges."""
    original_resolve = Path.resolve
    outside = tmp_path.parent / "outside"

    def resolve(path, *args, **kwargs):
        if path == tmp_path / "linked":
            return outside
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve)
    with pytest.raises(ValueError, match="outside"):
        validate_service_checkout(PipelineServiceContract(port=8080, context="linked"), tmp_path)


def test_registration_normalizes_identity_and_selects_exact_branch():
    registration = PipelineRegistration.model_validate(registration_data())
    assert registration.source_repository == "example/sample-api"
    assert registration.environment_for(source_change()).name == "test"
    assert not registration.environment_for(source_change()).active
    for change in [source_change(branch="main"), source_change(repository="other/repo")]:
        with pytest.raises(ValueError):
            registration.environment_for(change)


@pytest.mark.parametrize("commit", ["main", "c" * 12, "0" * 40, "g" * 40, "c" * 41])
def test_only_full_nonzero_source_revisions_are_admitted(commit):
    with pytest.raises(ValidationError):
        source_change(commit=commit)


@pytest.mark.parametrize(
    "branch", ["refs/heads/test", "test..prod", "test.lock", "test/", "-test", "te st", "test@{1}"]
)
def test_registration_rejects_ambiguous_branch_names(branch):
    data = registration_data()
    data["environments"][0]["branch"] = branch
    with pytest.raises(ValidationError):
        PipelineRegistration.model_validate(data)


@pytest.mark.parametrize(
    "repository",
    ["https://github.com/example/app", "../app", "example/app.git", "example/app/extra"],
)
def test_registration_rejects_repository_urls_and_ambiguous_names(repository):
    with pytest.raises(ValidationError):
        PipelineRegistration.model_validate(registration_data(source_repository=repository))


@pytest.mark.parametrize(
    "image",
    [
        "app",
        "registry.example/app:latest",
        "registry.example/app@sha256:abc",
        "https://registry.example/app",
        "registry.example/../app",
    ],
)
def test_destinations_are_qualified_untagged_image_repositories(image):
    data = registration_data()
    data["environments"][0]["image_repository"] = image
    with pytest.raises(ValidationError):
        PipelineRegistration.model_validate(data)


def test_registry_port_and_nested_branches_are_supported():
    data = registration_data()
    data["environments"][0].update(
        branch="deploy/test", image_repository="registry.example:5000/sample/app"
    )
    assert PipelineRegistration.model_validate(data).environments[0].branch == "deploy/test"


@pytest.mark.parametrize("field", ["name", "branch", "image_repository"])
def test_registration_rejects_duplicate_environment_routes(field):
    data = registration_data()
    data["environments"][1][field] = data["environments"][0][field]
    with pytest.raises(ValidationError):
        PipelineRegistration.model_validate(data)


def test_production_cannot_use_the_test_deactivation_switch():
    data = registration_data()
    data["environments"][1]["active"] = False
    with pytest.raises(ValidationError):
        PipelineRegistration.model_validate(data)


@pytest.mark.parametrize("collision", ["identity", "repository", "release", "image"])
def test_catalog_rejects_conflicts_across_services(collision):
    first = registration_data()
    second = registration_data(service_id="another-api", source_repository="example/another")
    for env in second["environments"]:
        env["release"] = "another-" + env["name"]
        env["image_repository"] = "registry.example/another-" + env["name"] + "/app"
    if collision == "identity":
        second["service_id"] = first["service_id"]
    elif collision == "repository":
        second["source_repository"] = first["source_repository"].upper()
    elif collision == "release":
        second["environments"][0]["release"] = first["environments"][0]["release"]
    else:
        second["environments"][0]["image_repository"] = first["environments"][0]["image_repository"]
    with pytest.raises(ValidationError):
        PipelineCatalog(registrations=[first, second])


def test_schema_round_trip_preserves_unknown_field_rejection():
    registration = PipelineRegistration.model_validate(registration_data())
    catalog = PipelineCatalog(registrations=[registration])
    assert PipelineCatalog.model_validate_json(catalog.model_dump_json()) == catalog
    schema = PipelineServiceContract.model_json_schema()
    assert schema["additionalProperties"] is False
    assert json.loads(catalog.model_dump_json())["registrations"][0]["service_id"] == "sample-api"


def test_generic_example_contracts_load_without_project_specific_code():
    example = Path("examples/pipeline")
    service = PipelineServiceContract.model_validate_json((example / "service.json").read_text())
    catalog = PipelineCatalog.model_validate_json((example / "catalog.json").read_text())
    assert service.port == 8080
    assert catalog.registrations[0].environment_for(source_change()).name == "test"


@pytest.mark.parametrize(
    "path", ["https://other/health", "//other/health", "/health?token=x", "/a\nb"]
)
def test_health_check_cannot_select_another_host(path):
    with pytest.raises(ValidationError):
        PipelineServiceContract(port=8080, health_path=path)


def test_distinct_environment_names_cannot_share_one_release():
    data = registration_data()
    for field in ("target", "namespace", "release"):
        data["environments"][1][field] = data["environments"][0][field]
    with pytest.raises(ValidationError, match="distinct release"):
        PipelineRegistration.model_validate(data)
