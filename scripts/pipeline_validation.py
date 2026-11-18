"""Retain full-suite, Linux filesystem, frontend and Helm checks for one revision."""

import hashlib
import json
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def snapshot():
    files = (
        subprocess.check_output(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=ROOT
        )
        .decode()
        .split("\0")
    )
    return {
        name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
        for name in files
        if name and (ROOT / name).is_file()
    }


def validate(proof, category):
    output = proof.output / category
    output.mkdir(parents=True, exist_ok=True)
    before = snapshot()
    commands = []

    def run(name, args, cwd=ROOT, timeout=1800):
        path = output / (name + ".log")
        with path.open("w", encoding="utf-8") as stream:
            result = subprocess.run(
                args, cwd=cwd, stdout=stream, stderr=subprocess.STDOUT, timeout=timeout
            )
        commands.append(
            {
                "argv": args,
                "exit_code": result.returncode,
                "log": str(path.relative_to(proof.output)),
            }
        )
        if result.returncode:
            raise RuntimeError(f"{name} failed; see {path}")

    if category == "python-lint-linux":
        run("lint", [sys.executable, "-m", "ruff", "check", "."])
        native_xml = output / "python.xml"
        run(
            "python", [sys.executable, "-m", "pytest", "-q", "-ra", "--junitxml=" + str(native_xml)]
        )
        skipped = [
            case.attrib
            for case in ET.parse(native_xml).iter("testcase")
            if case.find("skipped") is not None
        ]
        if any(
            case["classname"] not in {
                "tests.test_kubernetes_artifacts",
                "tests.test_make_flags",
                "tests.test_docker_artifacts",
            }
            for case in skipped
        ):
            raise RuntimeError("full Python suite skipped a test outside the Linux supplement")
        dockerfile = output / "Dockerfile"
        image = proof.state["images"]["controller"]["digest"]
        dockerfile.write_text(
            f"FROM {image}\nUSER root\n"
            "RUN apt-get update && apt-get install -y make && rm -rf /var/lib/apt/lists/*\n"
            "RUN pip install --no-cache-dir pytest pytest-asyncio httpx\n"
            "COPY tests /app/tests\nCOPY scripts /app/scripts\nCOPY Makefile /app/Makefile\n"
            "WORKDIR /app\n",
            encoding="utf-8",
        )
        tag = "goblin-king:acceptance-linux-" + proof.state["run_id"][:8]
        run("linux-build", ["docker", "build", "-t", tag, "-f", str(dockerfile), str(ROOT)])
        run(
            "linux",
            [
                "docker",
                "run",
                "--rm",
                "--mount",
                f"type=bind,source={output},target=/proof",
                "--entrypoint",
                "python",
                tag,
                "-m",
                "pytest",
                "tests/test_kubernetes_artifacts.py",
                "tests/test_make_flags.py",
                "tests/test_docker_artifacts.py",
                "-q",
                "-ra",
                "--junitxml=/proof/linux.xml",
            ],
        )
        if list(ET.parse(output / "linux.xml").iter("skipped")):
            raise RuntimeError("Linux filesystem supplement skipped required coverage")
        (output / "linux-image.json").write_bytes(
            subprocess.check_output(
                [
                    "docker",
                    "image",
                    "inspect",
                    tag,
                ]
            )
        )
    elif category == "frontend-helm":
        npm = shutil.which("npm")
        if not npm:
            raise RuntimeError("npm is required for frontend acceptance")
        run("frontend-tests", [npm, "test"], ROOT / "admin-ui")
        run("frontend-build", [npm, "run", "build"], ROOT / "admin-ui")
        run(
            "control-chart",
            [
                "helm",
                "lint",
                str(ROOT / "charts/goblin-king"),
                "-f",
                str(proof.output / "delivery/values.json"),
            ],
        )
        run(
            "service-chart",
            [
                "helm",
                "lint",
                str(ROOT / "charts/pipeline-http-service"),
                "--set",
                "image=registry.invalid/app@sha256:" + "a" * 64,
            ],
        )
    else:
        raise ValueError("unknown validation category")
    if snapshot() != before:
        raise RuntimeError("source changed during validation; rerun against the final revision")
    summary = {"revision": proof.state["revision"], "source_sha256": before, "commands": commands}
    (output / "checks.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return {"evidence": str((output / "checks.json").relative_to(proof.output))}
