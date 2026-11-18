"""Docker result paths remain downloadable without escaping a job directory."""

from pathlib import Path

import pytest

from goblin_king.api_artifacts import artifact_file_path
from goblin_king.contracts import GoblinResult
from goblin_king.docker_artifacts import normalize_docker_artifacts


@pytest.mark.parametrize(
    ("uri", "container_root"),
    [
        ("report.txt", "/artifacts"),
        ("/artifacts/report.txt", "/artifacts"),
        ("file:///artifacts/report.txt", "/artifacts"),
        ("file:///goblin-data/artifacts/job-a/report.txt", "/goblin-data/artifacts/job-a"),
    ],
)
def test_docker_artifact_resolves_for_api(tmp_path: Path, uri: str, container_root: str):
    root = tmp_path / "job-a"
    root.mkdir()
    report = root / "report.txt"
    report.write_text("download proof", encoding="utf-8")
    result = GoblinResult.ok(artifacts=[{"name": "report.txt", "uri": uri}])
    normalized = normalize_docker_artifacts(result, root, container_root)
    location = artifact_file_path(tmp_path, normalized.artifacts[0])
    assert location == report
    assert location.read_text(encoding="utf-8") == "download proof"


@pytest.mark.parametrize(
    "uri",
    [
        "../other-job/secret.txt",
        "file:///goblin-data/artifacts/other-job/secret.txt",
        "file://remote/artifacts/report.txt",
        "file:///artifacts/%2e%2e/secret.txt",
        "C:\\secret.txt",
        "sub\\..\\secret.txt",
        "https://example.com/report.txt",
        "missing.txt",
        ".",
    ],
)
def test_docker_artifact_rejects_unretained_paths(tmp_path: Path, uri: str):
    result = GoblinResult.ok(artifacts=[{"name": "report.txt", "uri": uri}])
    with pytest.raises((ValueError, OSError)):
        normalize_docker_artifacts(result, tmp_path, "/artifacts")


def test_docker_artifact_rejects_symlink(tmp_path: Path):
    target = tmp_path / "target.txt"
    target.write_text("proof", encoding="utf-8")
    try:
        (tmp_path / "link.txt").symlink_to(target)
    except OSError as error:
        if getattr(error, "winerror", None) == 1314:
            pytest.skip("Windows symlink privilege unavailable; exercised by Linux checks")
        raise
    result = GoblinResult.ok(artifacts=[{"name": "report.txt", "uri": "link.txt"}])
    with pytest.raises(ValueError, match="symbolic"):
        normalize_docker_artifacts(result, tmp_path, "/artifacts")
