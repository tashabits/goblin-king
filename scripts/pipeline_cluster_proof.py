"""Install an isolated foundation fixture and retain reproducible persistence evidence.

The image must already be available to the selected cluster. This does not build
application images, dispatch workers, deploy a service, or exercise Git events.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]


class ClusterProof:
    """Scope every mutation to a newly created namespace and retain command evidence."""

    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.namespace = f"gk-pipeline-proof-{uuid4().hex[:10]}"
        self.output = args.output.resolve()
        self.output.mkdir(parents=True, exist_ok=False)
        self.kubectl = ["kubectl", "--context", args.context, "-n", self.namespace]
        self.helm = ["helm", "--kube-context", args.context, "-n", self.namespace]
        self.summary: dict = {
            "namespace": self.namespace,
            "context": args.context,
            "node": args.node,
            "image": args.image,
            "result": "incomplete",
        }

    def command(self, command: list[str], *, stdin: str | None = None, cwd: Path = ROOT) -> str:
        """Run bounded argv commands, retaining stdout/stderr without shell interpolation."""
        result = subprocess.run(
            command,
            input=stdin,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=360,
            cwd=cwd,
        )
        with (self.output / "commands.log").open("a", encoding="utf-8") as log:
            log.write(json.dumps({"argv": command, "cwd": str(cwd)}) + "\n")
            log.write(result.stdout + result.stderr + "\n")
        if result.returncode:
            raise RuntimeError(f"Command failed ({result.returncode}): {command}: {result.stderr}")
        return result.stdout

    def save(self, name: str, value: object) -> None:
        """Keep structured observations alongside the exact command transcript."""
        (self.output / name).write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")

    def pods(self) -> list[dict]:
        """Read pod identities and image IDs for replacement and placement assertions."""
        return json.loads(self.command(self.kubectl + ["get", "pods", "-o", "json"]))["items"]

    def verify_placement(self, pods: list[dict]) -> dict[str, str]:
        """Require all three SQLite clients to be ready on the approved node."""
        identities = {}
        for component in ("api", "scheduler", "directory-api"):
            selected = [
                pod
                for pod in pods
                if pod["metadata"]["labels"].get("app.kubernetes.io/component") == component
            ]
            if len(selected) != 1 or selected[0]["spec"]["nodeName"] != self.args.node:
                raise RuntimeError(f"unexpected placement or replica count for {component}")
            if not all(item["ready"] for item in selected[0]["status"]["containerStatuses"]):
                raise RuntimeError(f"{component} is not ready")
            identities[component] = selected[0]["metadata"]["uid"]
        return identities

    def probe(self, component: str, expected: str | None = None, backup: bool = False) -> dict:
        """Execute only the fixture admission probe in an installed storage client."""
        command = self.kubectl + [
            "exec",
            f"deploy/foundation-{component}",
            "--",
            "python",
            "-m",
            "examples.pipeline.persistence_probe",
            "--db",
            "/data/goblin-king.sqlite3",
        ]
        if expected:
            command += ["--expected-build-id", expected]
        if backup:
            command += ["--backup", "/data/foundation-backup.sqlite3"]
        return json.loads(self.command(command))

    def http_probe(self) -> dict:
        """Prove real HTTP auth and persisted audit reads through the installed API service."""
        source = """
import json
from urllib.request import Request, urlopen
from urllib.error import HTTPError
base = "http://foundation-api:8000"
def call(path, body=None, authenticated=False):
    headers = {"Content-Type": "application/json"}
    if authenticated:
        headers["Authorization"] = "Bearer foundation-fixture-token"
    request = Request(base + path, data=json.dumps(body).encode() if body else None,
                      headers=headers)
    try:
        with urlopen(request, timeout=15) as response:
            return response.status, json.load(response)
    except HTTPError as error:
        return error.code, None
assert call("/health")[0] == 200
denied, _ = call("/admin/projects", {"name": "must-not-exist"})
assert denied == 401, denied
status, project = call("/admin/projects", {"name": "foundation-http-proof"}, True)
assert status == 200, status
status, audit = call("/audit-logs", authenticated=True)
assert status == 200, status
assert any(item["resource_id"] == project["id"] for item in audit["items"])
print(json.dumps({"health": 200, "unauthenticated_mutation": denied,
                  "authenticated_mutation": 200, "persisted_audit_read": 200,
                  "project_id": project["id"]}))
"""
        return json.loads(
            self.command(
                self.kubectl + ["exec", "-i", "deploy/foundation-api", "--", "python", "-"],
                stdin=source,
            )
        )

    def run(self) -> None:
        """Install, upgrade, replace pods, restore a backup, and optionally remove the fixture."""
        self.summary["source_revision"] = self.command(["git", "rev-parse", "HEAD"]).strip()
        self.summary["source_dirty"] = bool(self.command(["git", "status", "--porcelain"]).strip())
        self.save("source-diff.json", self.command(["git", "diff", "HEAD"]))
        source_files = self.command(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"]
        ).splitlines()
        self.save(
            "source-sha256.json",
            {
                name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                for name in source_files
                if (ROOT / name).is_file()
            },
        )
        self.save(
            "nodes.json", json.loads(self.command(self.kubectl + ["get", "nodes", "-o", "json"]))
        )
        self.summary["helm_version"] = self.command(["helm", "version", "--short"]).strip()
        self.command(self.kubectl + ["create", "namespace", self.namespace])
        namespace_uid = json.loads(
            self.command(self.kubectl + ["get", "namespace", self.namespace, "-o", "json"])
        )["metadata"]["uid"]
        self.summary["namespace_uid"] = namespace_uid
        repository, tag = self.args.image.rsplit(":", 1)
        values = {
            "image": {"repository": repository, "tag": tag, "pullPolicy": "IfNotPresent"},
            "persistence": {
                "topology": "sqlite-single-node",
                "sqliteNode": self.args.node,
                "storageClassName": self.args.storage_class,
            },
            "api": {
                "bootstrapAdminToken": "foundation-fixture-token",
                "resources": {
                    "requests": {"cpu": "100m", "memory": "128Mi"},
                    "limits": {"cpu": "1", "memory": "512Mi"},
                },
            },
            "repository": {"enabled": True},
            "admin": {"enabled": False, "ingress": {"enabled": False}},
            "workers": {"exampleLongHello": {"enabled": False}},
            "config": {"redisUrl": "redis://foundation-redis:6379/0"},
        }
        values_path = self.output / "values.json"
        self.save("values.json", values)
        install = self.helm + [
            "upgrade",
            "--install",
            "foundation",
            "charts/goblin-king",
            "-f",
            str(values_path),
            "--wait",
            "--timeout",
            "5m",
        ]
        print(f"Installing {self.namespace}", flush=True)
        self.command(install)
        before = self.pods()
        before_uids = self.verify_placement(before)
        self.save("pods-before.json", before)
        self.summary["admission"] = self.probe("api")
        build_id = self.summary["admission"]["build_id"]
        self.summary["cross_process_replay"] = self.probe("scheduler", build_id)
        self.summary["http"] = self.http_probe()
        print("Upgrading and replacing storage clients", flush=True)
        self.command(install)
        for component in ("api", "scheduler", "directory-api"):
            self.command(
                self.kubectl + ["rollout", "restart", f"deployment/foundation-{component}"]
            )
        for component in ("api", "scheduler", "directory-api"):
            self.command(
                self.kubectl
                + ["rollout", "status", f"deployment/foundation-{component}", "--timeout=120s"]
            )
        after = self.pods()
        after_uids = self.verify_placement(after)
        if any(before_uids[key] == after_uids[key] for key in before_uids):
            raise RuntimeError("a storage client was not replaced")
        self.save("pods-after.json", after)
        api_pod = next(
            pod["metadata"]["name"]
            for pod in after
            if pod["metadata"]["labels"].get("app.kubernetes.io/component") == "api"
        )
        self.summary["restart_replay"] = self.probe("directory-api", build_id, backup=True)
        project_id = self.summary["http"]["project_id"]
        audit_read = """
import json, sys
from urllib.request import Request, urlopen
request = Request("http://foundation-api:8000/audit-logs", headers={
    "Authorization": "Bearer foundation-fixture-token"})
with urlopen(request, timeout=15) as response:
    data = json.load(response)
assert any(item["resource_id"] == sys.argv[1] for item in data["items"])
print(json.dumps({"project_id": sys.argv[1], "persisted_after_restart": True}))
"""
        self.summary["http_after_restart"] = json.loads(
            self.command(
                self.kubectl
                + ["exec", "-i", "deploy/foundation-api", "--", "python", "-", project_id],
                stdin=audit_read,
            )
        )
        # Copy the backup off the PVC, then into the new API pod's ephemeral filesystem.
        # Verifying there demonstrates that reopening the original PVC is not the restore test.
        self.command(
            self.kubectl
            + [
                "cp",
                f"{api_pod}:/data/foundation-backup.sqlite3",
                "backup.sqlite3",
            ],
            cwd=self.output,
        )
        self.command(
            self.kubectl
            + [
                "cp",
                "backup.sqlite3",
                f"{api_pod}:/tmp/restored.sqlite3",
            ],
            cwd=self.output,
        )
        restored = self.command(
            self.kubectl
            + [
                "exec",
                "deploy/foundation-api",
                "--",
                "python",
                "-m",
                "examples.pipeline.persistence_probe",
                "--db",
                "/tmp/restored.sqlite3",
                "--expected-build-id",
                build_id,
            ]
        )
        self.summary["restored_backup"] = json.loads(restored)
        self.save(
            "helm-history.json",
            json.loads(self.command(self.helm + ["history", "foundation", "-o", "json"])),
        )
        self.save("pvc.json", json.loads(self.command(self.kubectl + ["get", "pvc", "-o", "json"])))
        self.summary["result"] = "passed"
        self.summary["cleanup"] = "retained"
        if self.args.cleanup:
            current = json.loads(
                self.command(self.kubectl + ["get", "namespace", self.namespace, "-o", "json"])
            )
            if current["metadata"]["uid"] != namespace_uid:
                raise RuntimeError("namespace ownership changed; refusing cleanup")
            self.command(self.helm + ["uninstall", "foundation", "--wait", "--timeout", "2m"])
            self.command(
                self.kubectl
                + ["delete", "namespace", self.namespace, "--wait=true", "--timeout=120s"]
            )
            self.summary["cleanup"] = "removed fixture namespace"


def main() -> None:
    """Require explicit targets and preserve failed installs for inspection."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context", required=True)
    parser.add_argument("--node", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--storage-class", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cleanup", action="store_true")
    args = parser.parse_args()
    if not re.fullmatch(r"[^\s@]+:[\w.-]+", args.image):
        parser.error("image must include an explicit tag")
    proof = ClusterProof(args)
    try:
        proof.run()
    except Exception as error:
        proof.summary["result"] = "failed"
        proof.summary["error"] = str(error)
        raise
    finally:
        proof.save("summary.json", proof.summary)
        print(json.dumps(proof.summary, indent=2), flush=True)


if __name__ == "__main__":
    main()
