"""Build one revision of the local delivery installation and retain immutable image identities."""

import argparse
import hashlib
import io
import json
import subprocess
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FOUNDATION = "be2192081291c005e1879ab9486451479152e784"


def source_identity():
    digest = hashlib.sha256()
    names = (
        subprocess.check_output(
            [
                "git",
                "ls-files",
                "--cached",
                "--others",
                "--exclude-standard",
                "-z",
            ],
            cwd=ROOT,
        )
        .decode()
        .split("\0")
    )
    for name in sorted(name for name in names if name and (ROOT / name).is_file()):
        digest.update(name.encode() + b"\0")
        digest.update(hashlib.sha256((ROOT / name).read_bytes()).digest())
    return digest.hexdigest()


def foundation(output):
    tag = "goblin-king:pipeline-foundation-" + FOUNDATION[:12]
    inspected = subprocess.run(["docker", "image", "inspect", tag], capture_output=True, text=True)
    existing = json.loads(inspected.stdout)[0] if inspected.returncode == 0 else None
    if (
        not existing
        or (existing["Config"].get("Labels") or {}).get("org.opencontainers.image.revision")
        != FOUNDATION
    ):
        source = output.parent / "foundation-source"
        source.mkdir(parents=True, exist_ok=True)
        raw = subprocess.check_output(["git", "archive", FOUNDATION], cwd=ROOT, timeout=60)
        with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
            archive.extractall(source, filter="data")
        with (output.parent / "foundation-build.log").open("w") as log:
            subprocess.run(
                [
                    "docker",
                    "build",
                    "-t",
                    tag,
                    "--label",
                    "org.opencontainers.image.revision=" + FOUNDATION,
                    str(source),
                ],
                stdout=log,
                stderr=subprocess.STDOUT,
                check=True,
            )
        existing = json.loads(subprocess.check_output(["docker", "image", "inspect", tag]))[0]
    evidence = {"tag": tag, "digest": existing["RepoDigests"][0], "source_revision": FOUNDATION}
    (output.parent / "foundation.json").write_text(json.dumps(evidence, indent=2))
    return evidence


def build(output, *, development=False):
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    dirty = bool(
        subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip()
    )
    if dirty and not development:
        raise RuntimeError("commit the implementation before building acceptance images")
    source_sha256 = source_identity()
    images = {}
    output.parent.mkdir(parents=True, exist_ok=True)
    foundation(output)
    for purpose, dockerfile, context in (
        ("controller", "Dockerfile", "."),
        ("builder", "workers/pipeline.build/Dockerfile", "."),
        ("provider", "examples/pipeline/Dockerfile.simulator", "."),
        ("deployer", "workers/pipeline.deploy/Dockerfile", "."),
        ("network", "workers/pipeline.network/Dockerfile", "."),
        ("admin", "admin-ui/Dockerfile", "admin-ui"),
    ):
        repository = "goblin-king-admin-ui" if purpose == "admin" else "goblin-king"
        tag = f"{repository}:pipeline-{purpose}-{revision[:12]}" + (
            "-dev-" + source_sha256[:12] if dirty else ""
        )
        args = [
            "docker",
            "build",
            "-t",
            tag,
            "-f",
            str(ROOT / dockerfile),
            "--label",
            "org.opencontainers.image.revision=" + revision,
            "--label",
            "io.goblin-king.source-dirty=" + str(dirty).lower(),
        ]
        if purpose not in {"controller", "admin"}:
            args += ["--build-arg", "CONTROL_PLANE_IMAGE=" + images["controller"]["tag"]]
        with (output.parent / f"{purpose}-build.log").open("w") as log:
            subprocess.run(
                args + [str(ROOT / context)],
                cwd=ROOT,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=True,
            )
        inspected = json.loads(
            subprocess.check_output(["docker", "image", "inspect", tag], text=True)
        )[0]
        images[purpose] = {
            "tag": tag,
            "digest": inspected["RepoDigests"][0],
            "source_revision": revision,
            "source_dirty": dirty,
            "source_sha256": source_sha256,
        }
        print(purpose + ": " + images[purpose]["digest"], flush=True)
    if (
        source_identity() != source_sha256
        or subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        != revision
    ):
        raise RuntimeError("source changed while images were being built; no manifest was accepted")
    output.write_text(json.dumps(images, indent=2))
    return images


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--development", action="store_true")
    args = parser.parse_args()
    build(args.output.resolve(), development=args.development)
