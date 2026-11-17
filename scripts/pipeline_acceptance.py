"""Resumable cluster acceptance with required scenarios and ownership-checked cleanup."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = (
    "fresh-installation",
    "first-repository",
    "reviewed-merges",
    "second-repository",
    "invalid-input",
    "duplicate-reordered-events",
    "missed-event",
    "build-failures",
    "failed-rollout",
    "release-contention",
    "interruptions",
    "recording-recovery",
    "test-off-on",
    "emergency-rollback",
    "identity-boundaries",
    "multiple-nodes",
    "storage-recovery",
    "upgrade",
    "network-policy",
    "python-lint-linux",
    "frontend-helm",
    "docker-admin-audit",
    "helm-admin-audit",
    "side-effect-faults",
    "cleanup",
)


class Acceptance:
    """Retain evidence across interruption without treating absent checks as passes."""

    def __init__(self, output: Path):
        self.output = output.resolve()
        self.state_path = self.output / "acceptance.json"
        self.state = json.loads(self.state_path.read_text()) if self.state_path.exists() else {}

    def save(self):
        self.output.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.state, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.state_path)

    def command(self, argv, *, document=None, timeout=120, environment=None):
        result = subprocess.run(
            argv,
            input=json.dumps(document) if document else None,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
            env=os.environ | (environment or {}),
        )
        with (self.output / "commands.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(
                json.dumps(
                    {
                        "at": datetime.now(UTC).isoformat(),
                        "argv": argv,
                        "code": result.returncode,
                        "stdout": result.stdout,
                        "stderr": result.stderr,
                    }
                )
                + "\n"
            )
        if result.returncode:
            raise RuntimeError(f"command failed: {argv[0]}: {result.stderr[-2000:]}")
        return result.stdout

    def kube(self, *args, document=None):
        return self.command(
            ["kubectl", "--context", self.state["context"], "--request-timeout=30s", *args],
            document=document,
        )

    def apply(self, resource):
        return self.kube("apply", "-f", "-", document=resource)

    def baseline(self, *, exclude_acceptance=False):
        kinds = "namespaces,deployments,statefulsets,daemonsets,services,pvc,networkpolicies"
        items = json.loads(self.kube("get", kinds, "-A", "-o", "json"))["items"]
        owned = (
            {
                item["metadata"]["name"]
                for item in items
                if item["kind"] == "Namespace"
                and item["metadata"].get("labels", {}).get("goblin-king.io/acceptance")
            }
            if exclude_acceptance
            else set()
        )
        return {
            item["metadata"]["uid"]: {
                "kind": item["kind"],
                "namespace": item["metadata"].get("namespace"),
                "name": item["metadata"]["name"],
                "spec_sha256": hashlib.sha256(
                    json.dumps(item.get("spec", {}), sort_keys=True).encode()
                ).hexdigest(),
            }
            for item in items
            if item["metadata"].get("namespace") not in owned
            and not (item["kind"] == "Namespace" and item["metadata"]["name"] in owned)
        }

    def prepare(self, context, image, images=None):
        if self.state:
            raise ValueError("output already has a run; use resume or a fresh output directory")
        self.state = {
            "schema": 1,
            "run_id": str(uuid4()),
            "context": context,
            "image": image,
            "images": images,
            "namespaces": {},
            "scenarios": {name: {"status": "pending"} for name in SCENARIOS},
        }
        self.save()
        self.state["revision"] = self.command(["git", "rev-parse", "HEAD"]).strip()
        self.state["source_status"] = self.command(["git", "status", "--porcelain"])
        self.state["baseline"] = self.baseline(exclude_acceptance=True)
        self.state["baseline_health"] = self.workload_health()
        self.state["nodes"] = json.loads(self.kube("get", "nodes", "-o", "json"))
        self.save()

    def workload_health(self):
        items = json.loads(
            self.kube("get", "deployments,statefulsets,daemonsets", "-A", "-o", "json")
        )["items"]
        baseline = self.state["baseline"]
        return {
            item["metadata"]["uid"]: {
                "kind": item["kind"],
                "namespace": item["metadata"]["namespace"],
                "name": item["metadata"]["name"],
                "ready": item.get("status", {}).get(
                    "numberReady" if item["kind"] == "DaemonSet" else "readyReplicas", 0
                ),
            }
            for item in items
            if item["metadata"]["uid"] in baseline
        }

    def verify_baseline_health(self):
        expected = self.state.get("baseline_health")
        if expected is None:
            raise RuntimeError(
                "baseline workload health was not recorded; use a fresh acceptance run"
            )
        deadline = time.monotonic() + 180
        while True:
            current = self.workload_health()
            unhealthy = [
                entry
                for uid, entry in expected.items()
                if uid not in current or current[uid]["ready"] < entry["ready"]
            ]
            if not unhealthy:
                (self.output / "preserved-workload-health.json").write_text(
                    json.dumps({"before": expected, "after": current}, indent=2), encoding="utf-8"
                )
                return
            if time.monotonic() > deadline:
                raise RuntimeError(
                    "unrelated workloads did not regain baseline health: " + str(unhealthy)
                )
            time.sleep(3)

    def namespace(self, purpose):
        if purpose in self.state["namespaces"]:
            entry = self.state["namespaces"][purpose]
            current = json.loads(self.kube("get", "namespace", entry["name"], "-o", "json"))
            if current["metadata"]["uid"] != entry["uid"]:
                raise RuntimeError("namespace identity changed; refusing to adopt it")
            return entry["name"]
        name = f"gk-accept-{self.state['run_id'][:8]}-{purpose}"
        resource = {
            "apiVersion": "v1",
            "kind": "Namespace",
            "metadata": {
                "name": name,
                "labels": {"goblin-king.io/acceptance": self.state["run_id"]},
            },
        }
        self.kube("create", "-f", "-", document=resource)
        current = json.loads(self.kube("get", "namespace", name, "-o", "json"))
        self.state["namespaces"][purpose] = {"name": name, "uid": current["metadata"]["uid"]}
        self.save()
        return name

    def network_policy(self):
        ns = self.namespace("policy")
        self.kube("-n", ns, "delete", "networkpolicy", "server-ingress", "--ignore-not-found")
        nodes = [node["metadata"]["name"] for node in self.state["nodes"]["items"]]
        if len(nodes) < 2:
            raise RuntimeError("two nodes are required")
        image = self.state["image"]
        for index, node in enumerate(nodes):
            for role in ("server", "allowed", "denied"):
                name = f"{role}-{index}"
                command = (
                    ["python", "-m", "http.server", "8080"]
                    if role == "server"
                    else ["python", "-c", "import time; time.sleep(7200)"]
                )
                self.apply(
                    {
                        "apiVersion": "v1",
                        "kind": "Pod",
                        "metadata": {"name": name, "namespace": ns, "labels": {"role": role}},
                        "spec": {
                            "nodeName": node,
                            "automountServiceAccountToken": False,
                            "restartPolicy": "Never",
                            "containers": [
                                {
                                    "name": "probe",
                                    "image": image,
                                    "command": command,
                                    "resources": {
                                        "requests": {"cpu": "10m", "memory": "32Mi"},
                                        "limits": {"cpu": "200m", "memory": "128Mi"},
                                    },
                                    "securityContext": {
                                        "runAsNonRoot": True,
                                        "runAsUser": 65532,
                                        "allowPrivilegeEscalation": False,
                                        "capabilities": {"drop": ["ALL"]},
                                        "seccompProfile": {"type": "RuntimeDefault"},
                                    },
                                }
                            ],
                        },
                    }
                )
        self.kube("-n", ns, "wait", "pod", "--all", "--for=condition=Ready", "--timeout=120s")
        pods = json.loads(self.kube("-n", ns, "get", "pods", "-o", "json"))["items"]
        addresses = [
            p["status"]["podIP"] for p in pods if p["metadata"]["labels"]["role"] == "server"
        ]

        def connect(role, index):
            code = (
                "import json,urllib.request; results=[]\n"
                f"for ip in {addresses!r}:\n"
                " try:\n  r=urllib.request.urlopen('http://'+ip+':8080',timeout=2); "
                "results.append(r.status==200)\n"
                " except OSError: results.append(False)\nprint(json.dumps(results))"
            )
            return json.loads(
                self.kube("-n", ns, "exec", f"{role}-{index}", "--", "python", "-c", code)
            )

        before = {
            f"{role}-{i}": connect(role, i)
            for i in range(len(nodes))
            for role in ("allowed", "denied")
        }
        if not all(all(value) for value in before.values()):
            raise RuntimeError("connectivity baseline failed before applying policy")
        self.apply(
            {
                "apiVersion": "networking.k8s.io/v1",
                "kind": "NetworkPolicy",
                "metadata": {"name": "server-ingress", "namespace": ns},
                "spec": {
                    "podSelector": {"matchLabels": {"role": "server"}},
                    "policyTypes": ["Ingress"],
                    "ingress": [
                        {
                            "from": [{"podSelector": {"matchLabels": {"role": "allowed"}}}],
                            "ports": [{"protocol": "TCP", "port": 8080}],
                        }
                    ],
                },
            }
        )
        deadline = time.monotonic() + 45
        while True:
            after = {
                f"{role}-{i}": connect(role, i)
                for i in range(len(nodes))
                for role in ("allowed", "denied")
            }
            correct = all(
                all(v) if k.startswith("allowed") else not any(v) for k, v in after.items()
            )
            if correct:
                break
            if time.monotonic() > deadline:
                raise RuntimeError(f"network policy is not enforced: {after}")
            time.sleep(1)
        evidence = {"before": before, "after": after, "pods": pods}
        (self.output / "network-policy.json").write_text(json.dumps(evidence, indent=2))
        return {"evidence": "network-policy.json", "namespace": ns}

    def run(self, only=None):
        from pipeline_admin_acceptance import verify as verify_admin
        from pipeline_validation import validate

        implementations = {"network-policy": self.network_policy, "cleanup": self.cleanup_scenario}
        implementations.update(
            {
                name: lambda name=name: validate(self, name)
                for name in ("python-lint-linux", "frontend-helm")
            }
        )
        implementations.update(
            {
                name + "-admin-audit": lambda name=name: verify_admin(self, name)
                for name in ("docker", "helm")
            }
        )
        if self.state.get("images"):
            from pipeline_delivery_acceptance import DeliveryAcceptance

            implementations.update(DeliveryAcceptance(self).implementations())
        for name in SCENARIOS:
            if only and name != only:
                continue
            if self.state["scenarios"][name]["status"] == "passed":
                continue
            self.state["scenarios"][name] = {"status": "running"}
            self.report()
            try:
                if name not in implementations:
                    raise RuntimeError("scenario implementation or prerequisites are not ready")
                evidence = implementations[name]()
                self.state["scenarios"][name] = {"status": "passed", **evidence}
            except Exception as error:
                self.state["scenarios"][name] = {"status": "failed", "reason": str(error)}
            self.report()
        return self.report()

    def cleanup_scenario(self):
        incomplete = [
            name
            for name in SCENARIOS
            if name != "cleanup" and self.state["scenarios"][name]["status"] != "passed"
        ]
        if incomplete:
            raise RuntimeError(
                "resources retained for incomplete acceptance: "
                + ", ".join(incomplete)
                + "; explicit cleanup remains available"
            )
        self.cleanup()
        return {"evidence": "cleanup.json"}

    def cleanup(self):
        # Verify every identity before uninstalling resources with cross-namespace bindings.
        for entry in self.state["namespaces"].values():
            if entry.get("removed"):
                continue
            current = json.loads(self.kube("get", "namespace", entry["name"], "-o", "json"))
            if (
                current["metadata"]["uid"] != entry["uid"]
                or current["metadata"].get("labels", {}).get("goblin-king.io/acceptance")
                != self.state["run_id"]
            ):
                raise RuntimeError("namespace ownership mismatch; cleanup refused")
        for entry in self.state["namespaces"].values():
            if entry["name"].endswith("-demo") and not entry.get("removed"):
                self.command(
                    [
                        "helm",
                        "--kube-context",
                        self.state["context"],
                        "uninstall",
                        "delivery",
                        "-n",
                        entry["name"],
                        "--ignore-not-found",
                        "--wait",
                        "--timeout=90s",
                    ]
                )
        for entry in self.state["namespaces"].values():
            if entry.get("removed"):
                continue
            current = json.loads(self.kube("get", "namespace", entry["name"], "-o", "json"))
            meta = current["metadata"]
            if (
                meta["uid"] != entry["uid"]
                or meta.get("labels", {}).get("goblin-king.io/acceptance") != self.state["run_id"]
            ):
                raise RuntimeError("namespace ownership mismatch; cleanup refused")
            self.kube("delete", "namespace", entry["name"], "--wait=true", "--timeout=90s")
            entry["removed"] = True
            self.save()
        current = self.baseline()
        changed = [v for uid, v in self.state["baseline"].items() if current.get(uid) != v]
        if changed:
            raise RuntimeError(f"unrelated resources changed: {changed}")
        self.verify_baseline_health()
        self.state["cleanup_evidence"] = {
            "preserved": len(self.state["baseline"]),
            "at": datetime.now(UTC).isoformat(),
        }
        (self.output / "cleanup.json").write_text(
            json.dumps(self.state["cleanup_evidence"], indent=2), encoding="utf-8"
        )
        self.save()

    def report(self):
        passed = all(self.state["scenarios"][name]["status"] == "passed" for name in SCENARIOS)
        images = self.state.get("images") or {}
        current_revision = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, timeout=30
        ).strip()
        current_status = subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=ROOT, text=True, timeout=30
        ).strip()
        self.state["report_source"] = {"revision": current_revision, "status": current_status}
        pinned = (
            {"controller", "builder", "provider", "deployer", "admin"}.issubset(images)
            and not self.state.get("source_status")
            and not current_status
            and current_revision == self.state["revision"]
            and all(
                item.get("source_revision") == self.state["revision"]
                and item.get("source_dirty") is False
                and "@sha256:" in item["digest"]
                for item in images.values()
            )
        )
        passed = passed and pinned
        self.state["pinned_implementation"] = pinned
        self.state["result"] = "passed" if passed else "incomplete"
        self.save()
        lines = [
            "# Pipeline cluster acceptance",
            "",
            f"Result: **{self.state['result']}**",
            "",
            f"Revision: `{self.state['revision']}`",
            f"Clean, pinned implementation: **{'yes' if pinned else 'no; development only'}**",
            "",
            "| Scenario | Status | Evidence / reason |",
            "| --- | --- | --- |",
        ]
        for name, item in self.state["scenarios"].items():
            detail = (
                item.get("reason", item.get("evidence", "")).replace("\n", " ").replace("|", "/")
            )
            lines.append(f"| {name} | {item['status']} | {detail} |")
        (self.output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        return passed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "run", "resume", "report", "cleanup"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--context", default="docker-desktop")
    parser.add_argument("--image", default="goblin-king:pipeline-foundation-proof-v2")
    parser.add_argument("--only", choices=SCENARIOS)
    parser.add_argument("--images", type=Path)
    args = parser.parse_args()
    proof = Acceptance(args.output)
    if args.command == "prepare":
        proof.prepare(
            args.context, args.image, json.loads(args.images.read_text()) if args.images else None
        )
    elif not proof.state:
        parser.error("prepare this output directory first")
    elif args.command == "cleanup":
        proof.cleanup()
    elif args.command in {"run", "resume"}:
        return 0 if proof.run(args.only) else 1
    else:
        return 0 if proof.report() else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
