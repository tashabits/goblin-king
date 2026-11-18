"""Operator-owned delivery configuration; secret values are never part of a catalog."""

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from goblin_king.pipeline_networking import NetworkingPolicy
from goblin_king.resource_policies import ResourcePolicy


def verify_installed_routes(registration, config):
    """Onboarding must match exact operator-owned source and deployment destinations."""
    source = config.get("sources", {}).get(registration.source_repository)
    if not source or not all(
        source.get(key)
        for key in (
            "namespace",
            "node",
            "github_secret",
            "publisher_secret",
            "pull_secret",
            "staging_repository",
        )
    ):
        raise ValueError("repository has no complete installed build configuration")
    for route in registration.environments:
        release = config.get("releases", {}).get(route.namespace + "/" + route.release)
        if route.image_repository not in source.get("image_repositories", []):
            raise ValueError(
                "registry destination differs from the installed repository configuration"
            )
        if (
            not release
            or release.get("environment") != route.name
            or not release.get("pull_secret")
        ):
            raise ValueError("release differs from the installed deployment configuration")
        if route.namespace == source["namespace"]:
            raise ValueError("source execution and application data require separate namespaces")


class PipelineSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    networking: NetworkingPolicy = Field(default_factory=NetworkingPolicy)
    simulated_provider: bool = False
    acceptance_faults: bool = False
    provider_url: str = "https://api.github.com"
    app_id: int = Field(default=1, gt=0)
    installation_id: int = Field(default=1, gt=0)
    private_key_path: Path = Path("/var/run/pipeline/github/private-key.pem")
    webhook_secret_path: Path = Path("/var/run/pipeline/github/webhook-secret")
    worker_image: str = ""
    worker_profile: str = "restricted-v1"
    deploy_image: str = ""
    network_image: str = ""
    release_repository: str = ""
    release_repository_id: int = Field(default=0, ge=0)
    notification_url: str = ""
    notification_token_path: Path = Path("/var/run/pipeline/notification/token")
    worker_policy: ResourcePolicy = Field(
        default_factory=lambda: ResourcePolicy.model_validate(
            {
                "timeout_seconds": 2700,
                "max_retries": 0,
                "cpu": {"request": "100m", "limit": "1"},
                "memory": {"request": "128Mi", "limit": "512Mi"},
                "filesystem": {"read_only_root": True, "tmpfs": ["/tmp:size=64Mi"]},
                "logs": {"max_bytes": 65536},
                "concurrency": {"max_running": 2, "max_project_running": 2},
            }
        )
    )
    pipeline_revision: str = ""
    namespace_prefixes: tuple[str, ...] = ()
    registry_prefixes: tuple[str, ...] = ()
    registry_endpoint: str = ""
    registry_reader_path: Path = Path("/var/run/pipeline/registry")
    deployment_profiles: tuple[str, ...] = ("http-service-v1",)
    recovery_mode: bool = False
    reconcile_seconds: int = Field(default=60, ge=1, le=60)

    @model_validator(mode="after")
    def local_faults_only(self):
        if self.acceptance_faults and not self.simulated_provider:
            raise ValueError("acceptance faults require the local simulated provider")
        return self
