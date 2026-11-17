"""Small trusted checkout and publication processes; source code is never executed here."""

from __future__ import annotations

import argparse
import hashlib
import json
import tarfile
from pathlib import Path, PurePosixPath
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener

from goblin_king.pipeline_contracts import PipelineBuildRequest, PipelineServiceContract
from goblin_king.pipeline_github import AppCredentials, GitHubProvider
from goblin_king.pipeline_oci import NoRedirect, RegistryClient, publish_oci


def checkout(document, root):
    request = PipelineBuildRequest.model_validate(document["request"])
    config = document["config"]
    provider = GitHubProvider(
        AppCredentials(
            config["app_id"],
            config["installation_id"],
            Path("/github/private-key.pem"),
            Path("/github/webhook-secret"),
            config["provider_url"],
            config.get("simulated_provider", False),
        )
    )
    token = provider.token(document["repository_id"])
    url = (
        f"{config['provider_url']}/repos/{request.source.repository}"
        f"/tarball/{request.source.commit}"
    )
    opener = build_opener(NoRedirect())
    http_request = Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        response = opener.open(http_request, timeout=30)
    except HTTPError as error:
        if error.code != 302:
            raise RuntimeError(f"checkout provider returned HTTP {error.code}") from None
        redirect = urlsplit(error.headers.get("Location", ""))
        if redirect.scheme != "https" or redirect.hostname != "codeload.github.com":
            raise ValueError("unexpected archive redirect") from None
        # Installation credentials must never follow an archive redirect.
        response = opener.open(Request(redirect.geturl()), timeout=30)
    archive = Path("/tmp/source.tar.gz")
    length = 0
    with response, archive.open("wb") as stream:
        while chunk := response.read(1024 * 1024):
            length += len(chunk)
            if length > 256 * 1024 * 1024:
                raise ValueError("source archive exceeds limit")
            stream.write(chunk)
    source = root / "source"
    source.mkdir(mode=0o775, exist_ok=False)
    (root / "artifact").mkdir(mode=0o775, exist_ok=False)
    (root / "artifact").chmod(0o775)
    total = 0
    paths = set()
    with tarfile.open(archive, "r:gz") as bundle:
        for member in bundle:
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts or len(path.parts) < 1:
                raise ValueError("source archive contains an unsafe path")
            relative = PurePosixPath(*path.parts[1:])
            if str(relative) == ".":
                continue
            if ".git" in relative.parts or member.issym() or member.islnk():
                raise ValueError("source archive contains a link or Git authentication material")
            destination = source.joinpath(*relative.parts)
            if member.isdir():
                destination.mkdir(mode=0o775, parents=True, exist_ok=True)
                continue
            total += member.size
            if (
                not member.isfile()
                or str(relative) in paths
                or total > 512 * 1024 * 1024
                or len(paths) > 50000
            ):
                raise ValueError("source archive entry exceeds extraction bounds")
            paths.add(str(relative))
            destination.parent.mkdir(mode=0o775, parents=True, exist_ok=True)
            with bundle.extractfile(member) as incoming, destination.open("xb") as outgoing:
                while chunk := incoming.read(1024 * 1024):
                    outgoing.write(chunk)
            destination.chmod(0o755 if member.mode & 0o111 else 0o644)
    contract = PipelineServiceContract.model_validate_json(
        (source / "pipeline-service.json").read_text()
    )
    if contract.model_dump(mode="json") != document["contract"]:
        raise ValueError("checkout contract differs from the verified source contract")
    with archive.open("rb") as stream:
        checksum = hashlib.file_digest(stream, "sha256").hexdigest()
    return {
        "commit": request.source.commit,
        "archive_sha256": checksum,
        "files": len(paths),
        "authentication_material_removed": True,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("step", choices=("checkout", "publish"))
    parser.add_argument("--document", default="/input/step.json", type=Path)
    args = parser.parse_args()
    document = json.loads(args.document.read_text())
    if args.step == "checkout":
        result = checkout(document, Path("/scratch"))
    else:
        from goblin_king.pipeline_faults import PublicationFaultGate

        client = RegistryClient(
            document["endpoint"],
            Path("/registry/username").read_text(),
            Path("/registry/password").read_text(),
            Path("/registry/ca.crt"),
            fault_hook=PublicationFaultGate(document.get("acceptance_fault", {})),
            fault_identity=document["repository"] + "/" + document["tag"],
        )
        result = publish_oci(
            client,
            Path("/scratch/artifact/image.tar"),
            document["repository"],
            document["tag"],
            document["labels"],
        )
    print(json.dumps(result, separators=(",", ":")), flush=True)


if __name__ == "__main__":
    main()
