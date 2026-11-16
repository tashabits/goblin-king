"""Render the supported SQLite topology and reject configurations that break it."""

from __future__ import annotations

import shutil
import subprocess

import pytest
import yaml

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="Helm is not installed")


def render(*settings: str) -> subprocess.CompletedProcess:
    """Render locally without requiring a Kubernetes connection."""
    command = ["helm", "template", "storage-proof", "charts/goblin-king"]
    for setting in (
        "persistence.topology=sqlite-single-node",
        "persistence.sqliteNode=proof-node",
        *settings,
    ):
        command.extend(["--set", setting])
    return subprocess.run(command, capture_output=True, text=True, check=False)


def test_storage_clients_share_node_volume_and_recreate_strategy() -> None:
    result = render("repository.enabled=true", "api.nodeSelector.disk=local")
    assert result.returncode == 0, result.stderr
    documents = list(yaml.safe_load_all(result.stdout))
    for component in ("api", "scheduler", "directory-api"):
        deployment = next(
            item
            for item in documents
            if item
            and item["kind"] == "Deployment"
            and item["metadata"]["name"] == f"storage-proof-{component}"
        )
        assert deployment["spec"]["replicas"] == 1
        assert deployment["spec"]["strategy"] == {"type": "Recreate"}
        pod = deployment["spec"]["template"]["spec"]
        assert pod["nodeSelector"]["kubernetes.io/hostname"] == "proof-node"
        volume = next(item for item in pod["volumes"] if item["name"] == "data")
        assert volume["persistentVolumeClaim"]["claimName"] == "storage-proof-data"
        if component == "api":
            assert pod["nodeSelector"]["disk"] == "local"
        if component != "scheduler":
            container = pod["containers"][0]
            for probe in ("startupProbe", "readinessProbe", "livenessProbe"):
                assert container[probe]["httpGet"]["path"] == "/health"


@pytest.mark.parametrize(
    "settings,message",
    [
        (("persistence.topology=unknown",), "persistence.topology"),
        (("persistence.enabled=false",), "persistence.enabled"),
        (("persistence.sqliteNode=",), "persistence.sqliteNode"),
        (("persistence.accessModes[0]=ReadWriteMany",), "ReadWriteOnce"),
        (("persistence.accessModes[0]=ReadWriteOncePod",), "ReadWriteOnce"),
        (("api.replicas=2",), "api.replicas=1"),
        (("scheduler.replicas=0",), "scheduler.replicas=1"),
        (("api.autoscaling.enabled=true",), "autoscaling disabled"),
        (("scheduler.autoscaling.enabled=true",), "autoscaling disabled"),
        (("repository.enabled=true", "repository.replicas=2"), "repository.replicas=1"),
        (("repository.enabled=true", "repository.autoscaling.enabled=true"), "autoscaling"),
        (("config.dbPath=/tmp/lost.sqlite3",), "config.dbPath"),
        (("config.dbPath=/data/../lost.sqlite3",), "config.dbPath"),
        (("config.projectSettingsPath=/app/project.json",), "projectSettingsPath"),
        (("project.extraVolumeMounts[0].mountPath=/data",), "extra mounts"),
        (("project.extraVolumeMounts[0].mountPath=/config/goblin-king-api.json",), "extra mounts"),
        ((r"api.nodeSelector.kubernetes\.io/hostname=other-node",), "conflicts"),
    ],
)
def test_unsafe_topology_is_rejected(settings: tuple[str, ...], message: str) -> None:
    result = render(*settings)
    assert result.returncode != 0
    assert message in result.stderr


def test_disabled_components_do_not_block_installation() -> None:
    result = render(
        "scheduler.enabled=false",
        "scheduler.replicas=0",
        "repository.enabled=false",
        "repository.replicas=0",
    )
    assert result.returncode == 0, result.stderr


def test_legacy_values_remain_renderable_without_storage_guarantees() -> None:
    result = render(
        "persistence.topology=legacy", "api.replicas=2", "scheduler.autoscaling.enabled=true"
    )
    assert result.returncode == 0, result.stderr
