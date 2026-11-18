"""Real-cluster delivery scenarios using only the local simulated provider."""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from pipeline_demo_prepare import DemoPreparation

ROOT = Path(__file__).resolve().parents[1]


HTTP_SCRIPT = """
import json, os, sys
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError
args = json.load(sys.stdin)
provider = args.pop('provider', False)
token = (Path('/data/fixture-token').read_text().strip() if provider
         else os.environ['GOBLIN_KING_BOOTSTRAP_ADMIN_TOKEN'])
body = args.get('body')
request = Request(('http://127.0.0.1:8770' if provider else 'http://127.0.0.1:8000') + args['path'],
    data=json.dumps(body).encode() if body is not None else None,
    headers={'Authorization':'Bearer ' + token, 'Content-Type':'application/json'})
try:
    with urlopen(request, timeout=120) as response:
        print(json.dumps({'status':response.status, 'body':json.load(response)}))
except HTTPError as error:
    print(json.dumps({'status':error.code, 'body':json.load(error)}))
"""


class DeliveryAcceptance:
    def __init__(self, acceptance):
        self.proof = acceptance
        self.output = acceptance.output / "delivery"
        self.prefix = "gk-a" + acceptance.state["run_id"][:8]
        self.control, self.provider = self.prefix + "-demo", self.prefix + "-provider"
        self.project = "acceptance"

    def request(self, path, body=None, *, provider=False, expected=(200, 201, 202)):
        namespace = self.provider if provider else self.control
        deployment = "provider" if provider else "delivery-api"
        response = json.loads(
            self.proof.kube(
                "-n",
                namespace,
                "exec",
                "-i",
                "deploy/" + deployment,
                "--",
                "python",
                "-c",
                HTTP_SCRIPT,
                document={"path": path, "body": body, "provider": provider},
            )
        )
        if response["status"] not in expected:
            raise RuntimeError(f"{path} returned {response['status']}: {response['body']}")
        return response["body"]

    def collection(self, name):
        return self.request(f"/pipeline/{name}?project_id={self.project}")["items"]

    def save(self, name, evidence):
        self.output.mkdir(parents=True, exist_ok=True)
        path = self.output / (name + ".json")
        path.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
        return {"evidence": str(path.relative_to(self.proof.output))}

    def wait(self, description, predicate, *, timeout=1800):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            result = predicate()
            if result:
                return result
            time.sleep(3)
        raise RuntimeError("timed out waiting for " + description)

    def checkpoint(self, name, action):
        checkpoints = self.proof.state.setdefault("delivery_checkpoints", {})
        if name not in checkpoints:
            checkpoints[name] = action()
            self.proof.save()
        return checkpoints[name]

    def installation(self, chart=None):
        images = self.proof.state.get("images")
        if not images:
            raise RuntimeError(
                "prepare requires a pinned --images manifest for delivery acceptance"
            )
        DemoPreparation(
            self.proof.state["context"],
            self.output,
            images,
            prefix=self.prefix,
            acceptance_id=self.proof.state["run_id"],
        ).prepare()
        items = json.loads(
            self.proof.kube(
                "get",
                "namespaces",
                "-l",
                "goblin-king.io/acceptance=" + self.proof.state["run_id"],
                "-o",
                "json",
            )
        )["items"]
        for item in items:
            name = item["metadata"]["name"]
            self.proof.state["namespaces"][name] = {"name": name, "uid": item["metadata"]["uid"]}
        self.proof.save()
        self.proof.command(
            [
                "helm",
                "--kube-context",
                self.proof.state["context"],
                "upgrade",
                "--install",
                "delivery",
                str(chart or ROOT / "charts/goblin-king"),
                "-n",
                self.control,
                "-f",
                str(self.output / "values.json"),
                "--wait=watcher",
                "--timeout=180s",
            ],
            timeout=210,
        )
        validation = self.request(
            "/admin/workers/validate-kubernetes",
            {
                "kinds": ["pipeline.build", "pipeline.deploy.test", "pipeline.deploy.prod"],
                "input": {"validate": True},
                "require_success": True,
                "timeout_seconds": 90,
            },
        )
        if not all(item["ok"] for item in validation["validations"]):
            raise RuntimeError("installed execution validation failed")
        self.request(
            "/_fixture/webhook",
            {"url": f"http://delivery-api.{self.control}.svc:8000/pipeline/webhooks/github"},
            provider=True,
        )
        return self.save(
            "installation",
            {
                "validation": validation,
                "status": self.request("/pipeline/status"),
                "resources": json.loads(
                    self.proof.kube(
                        "-n", self.control, "get", "deploy,pvc,networkpolicy", "-o", "json"
                    )
                ),
                "provider_mode": "local-simulation",
            },
        )

    def onboard(self, implementation, repository_id):
        values = json.loads((self.output / "values.json").read_text())
        revision = values["pipeline"]["settings"]["pipeline_revision"]
        source = values["pipeline"]["workerConfig"]["sources"][f"fixture/{implementation}-api"]
        registration = {
            "project_id": self.project,
            "service_id": implementation,
            "source_repository": f"fixture/{implementation}-api",
            "pipeline_revision": revision,
            "catalog_revision": revision,
            "environments": [
                {
                    "name": env,
                    "branch": branch,
                    "target": "local",
                    "namespace": f"{self.prefix}-{implementation}-{env}",
                    "release": "app",
                    "deployment_profile": "http-service-v1",
                    "image_repository": source["image_repositories"][index],
                }
                for index, (env, branch) in enumerate((("test", "test"), ("prod", "main")))
            ],
        }
        body = {"registration": registration, "repository_id": repository_id}
        discovery = self.request("/pipeline/repositories/discover", body)
        attached = self.request(
            "/pipeline/repositories",
            body | {"expected_version": 0, "idempotency_key": "attach-" + implementation},
        )
        before = self.collection("builds")
        if any(
            row["document"]["request"]["registration"]["service_id"] == implementation
            for row in before
        ):
            raise RuntimeError("attachment unexpectedly dispatched existing history")
        enabled = self.request(
            f"/pipeline/repositories/{attached['key']}/management",
            {
                "expected_version": attached["version"],
                "enabled": True,
                "idempotency_key": "enable-" + implementation,
            },
        )
        return {
            "discovery": discovery,
            "attached": attached,
            "enabled": enabled,
            "builds_before": before,
        }

    def first_repository(self):
        onboarding = self.checkpoint("onboard-python", lambda: self.onboard("python", 1))
        script = """
import os,subprocess
environment=os.environ | {'GOBLIN_KING_API_TOKEN':os.environ['GOBLIN_KING_BOOTSTRAP_ADMIN_TOKEN']}
subprocess.run(['goblin-king','pipeline','inspect','repositories','--project','acceptance'],
    env=environment,check=True)
"""
        cli = json.loads(
            self.proof.kube(
                "-n",
                self.control,
                "exec",
                "deploy/delivery-api",
                "--",
                "python",
                "-c",
                script,
            )
        )["items"]
        api = self.collection("repositories")
        if cli != api:
            raise RuntimeError("CLI and authenticated API repository state differ")
        return self.save("first-repository", {"onboarding": onboarding, "cli": cli, "api": api})

    def merge(self, name, implementation, branch, **options):
        return self.checkpoint(
            "merge-" + name,
            lambda: self.request(
                "/_fixture/merge",
                {
                    "repository": f"fixture/{implementation}-api",
                    "branch": branch,
                    "idempotency_key": self.proof.state["run_id"] + "/" + name,
                    **options,
                },
                provider=True,
            ),
        )

    def build(self, delivery):
        commit = delivery["event"]["after"]

        def settled():
            rows = [
                row
                for row in self.collection("builds")
                if row["document"]["request"]["source"]["commit"] == commit
            ]
            return next(
                (
                    row
                    for row in rows
                    if row["status"] in {"published", "superseded", "failed", "held"}
                ),
                None,
            )

        return self.wait("settled build " + commit, settled)

    def release(self, implementation, environment):
        return next(
            row
            for row in self.collection("releases")
            if row["document"]["environment"]["namespace"]
            == f"{self.prefix}-{implementation}-{environment}"
        )

    def deployed(self, implementation, environment, build_id):
        def recorded():
            row = self.release(implementation, environment)
            doc = row["document"]
            return (
                row
                if (
                    (doc["observed"] or {}).get("build_id") == build_id
                    and not doc["operation_id"]
                    and doc.get("recording")
                )
                else None
            )

        return self.wait("healthy recorded release", recorded, timeout=900)

    def route_health(self, release):
        document = release["document"]
        observed = document["observed"]
        namespace = document["environment"]["namespace"]
        name = document["environment"]["release"]
        url = f"http://{name}.{namespace}.svc:{observed['port']}{observed['health_path']}"
        response = json.loads(
            self.proof.kube(
                "-n",
                self.control,
                "exec",
                "-i",
                "deploy/delivery-api",
                "--",
                "python",
                "-c",
                "import json,sys; from urllib.request import urlopen; "
                "url=json.load(sys.stdin)['url']; response=urlopen(url,timeout=10); "
                "print(json.dumps({'url':url,'status':response.status,'body':json.load(response)}))",
                document={"url": url},
            )
        )
        expected = json.loads(observed["health"]["body"])
        if response["status"] != 200 or any(
            response["body"].get(key) != expected.get(key)
            for key in ("revision", "sentinel", "implementation", "healthy")
        ):
            raise RuntimeError("fresh route health differs from the observed healthy release")
        return response

    def reviewed_merges(self):
        evidence = {}
        for environment, branch in (("test", "test"), ("prod", "main")):
            delivery = self.merge("python-" + environment, "python", branch)
            build = self.build(delivery)
            if build["status"] != "published":
                raise RuntimeError("approved fixture build did not publish")
            observed = self.deployed("python", environment, build["id"])
            evidence[environment] = {"delivery": delivery, "build": build, "release": observed}
        return self.save("reviewed-merges-simulated", evidence)

    def second_repository(self):
        onboarding = self.checkpoint("onboard-node", lambda: self.onboard("node", 2))
        evidence = {"onboarding": onboarding}
        for environment, branch in (("test", "test"), ("prod", "main")):
            build = self.build(self.merge("node-" + environment, "node", branch))
            if build["status"] != "published":
                raise RuntimeError("second fixture build failed")
            evidence[environment] = self.deployed("node", environment, build["id"])
        return self.save("second-repository", evidence)

    def missed_event(self):
        delivery = self.merge("missed", "node", "main", webhook=False)
        build = self.build(delivery)
        if not build["document"]["request"]["source"]["delivery_id"].startswith("poll-"):
            raise RuntimeError("missed event was not admitted by polling")
        return self.save(
            "missed-event", {"build": build, "release": self.deployed("node", "prod", build["id"])}
        )

    def build_failures(self):
        evidence = {}
        for fault in ("test", "build"):

            def check_failure(fault=fault):
                previous = self.checkpoint(
                    "before-v2-" + fault, lambda: self.release("python", "test")
                )
                build = self.build(self.merge("failure-v2-" + fault, "python", "test", fault=fault))
                after = self.release("python", "test")
                if (
                    build["status"] != "failed"
                    or after["document"]["observed"] != previous["document"]["observed"]
                ):
                    raise RuntimeError(
                        "failed application work changed the healthy observed release"
                    )
                if after["document"]["recording"] != previous["document"]["recording"]:
                    raise RuntimeError("application failure changed the successful release record")
                return {"build": build, "preserved": after}

            evidence[fault] = self.checkpoint("verified-v2-" + fault, check_failure)

            def repair_failure(fault=fault):
                repair = self.build(
                    self.merge("repair-v2-" + fault, "python", "test", fault="repair")
                )
                return self.deployed("python", "test", repair["id"])

            self.checkpoint("repaired-v2-" + fault, repair_failure)
        namespace = self.prefix + "-python-build"

        def publication_failure():
            # The private checkpoint survives termination during credential fault injection.
            # No credential is included in the public evidence or command log.
            backup = self.output / "publication-secret.private.json"
            current = json.loads(
                subprocess.check_output(
                    [
                        "kubectl",
                        "--context",
                        self.proof.state["context"],
                        "-n",
                        namespace,
                        "get",
                        "secret",
                        "registry-publisher",
                        "-o",
                        "json",
                    ],
                    text=True,
                    encoding="utf-8",
                    timeout=30,
                )
            )
            if not backup.exists():
                temporary = backup.with_suffix(".tmp")
                temporary.write_text(json.dumps(current), encoding="utf-8")
                temporary.replace(backup)
            original = json.loads(backup.read_text())
            if current["metadata"]["uid"] != original["metadata"]["uid"]:
                raise RuntimeError("publication Secret identity changed; fault restoration refused")
            previous = self.checkpoint(
                "before-publication-v2", lambda: self.release("python", "test")
            )
            secret = {
                "apiVersion": "v1",
                "kind": "Secret",
                "metadata": {"name": "registry-publisher", "namespace": namespace},
                "data": original["data"] | {"password": "aW5qZWN0ZWQtaW52YWxpZA=="},
            }
            self.proof.apply(secret)
            try:
                failed = self.build(self.merge("publication-failure-v2", "python", "test"))
                preserved = self.release("python", "test")
                if (
                    failed["status"] != "failed"
                    or preserved["document"]["observed"] != previous["document"]["observed"]
                    or preserved["document"]["recording"] != previous["document"]["recording"]
                ):
                    raise RuntimeError("publication failure changed the healthy release or record")
                return {"failed": failed, "preserved": preserved}
            finally:
                secret["data"] = original["data"]
                self.proof.apply(secret)

        publication = self.checkpoint("verified-publication-v2", publication_failure)
        failed, preserved = publication["failed"], publication["preserved"]

        def rebuild():
            current = next(row for row in self.collection("builds") if row["id"] == failed["id"])
            return self.request(
                f"/pipeline/builds/{failed['id']}/rebuild",
                {"expected_version": current["version"], "idempotency_key": "retry-publication-v2"},
            )

        retry = self.checkpoint(
            "publication-rebuild-v2",
            rebuild,
        )
        if retry["build_id"] == failed["id"]:
            raise RuntimeError("explicit rebuild reused its previous publication identity")
        rebuilt = self.wait(
            "successful explicit rebuild",
            lambda: next(
                (
                    row
                    for row in self.collection("builds")
                    if row["id"] == retry["build_id"] and row["status"] == "published"
                ),
                None,
            ),
        )
        final = self.deployed("python", "test", rebuilt["id"])
        evidence["publication"] = {
            "failed": failed,
            "preserved": preserved,
            "retry": rebuilt,
            "final": final,
        }
        return self.save("build-failures", evidence)

    def action(self, name, implementation, environment, action, payload=None):
        def begin():
            row = self.release(implementation, environment)
            return self.request(
                f"/pipeline/releases/{row['key']}/operations",
                {
                    "expected_version": row["version"],
                    "action": action,
                    "payload": payload or {},
                    "idempotency_key": name,
                },
            )

        return self.checkpoint("action-" + name, begin)

    def operation(self, operation_id):
        return self.wait(
            "settled operation",
            lambda: next(
                (
                    row
                    for row in self.collection("operations")
                    if row["id"] == operation_id
                    and row["status"] in {"completed", "failed", "held"}
                ),
                None,
            ),
            timeout=900,
        )

    def test_off_on(self):
        namespace = self.prefix + "-python-test"

        def prepare_data():
            return json.loads(
                self.proof.kube(
                    "-n",
                    namespace,
                    "exec",
                    "deploy/app",
                    "--",
                    "python",
                    "-c",
                    "import json; from pathlib import Path; from uuid import uuid4; "
                    "p=Path('/data/acceptance-sentinel'); "
                    "p.write_text(str(uuid4())) if not p.exists() else None; "
                    "print(json.dumps({'sentinel':p.read_text()}))",
                )
            )

        sentinel = self.checkpoint("off-data-challenge", prepare_data)
        before = self.checkpoint("before-off", lambda: self.release("python", "test"))
        off = self.operation(
            self.action("test-off", "python", "test", "deactivate")["operation_id"]
        )
        if off["status"] != "completed":
            raise RuntimeError("deactivation failed")
        resources = json.loads(
            self.proof.kube(
                "-n", self.prefix + "-python-test", "get", "deploy,cronjobs,pods,pvc", "-o", "json"
            )
        )
        for item in resources["items"]:
            if item["kind"] == "Deployment" and item["spec"]["replicas"] != 0:
                raise RuntimeError("inactive Deployment has replicas")
            if item["kind"] == "CronJob" and not item["spec"]["suspend"]:
                raise RuntimeError("inactive schedule is not suspended")
            if item["kind"] == "Pod":
                raise RuntimeError("scheduled or background Pod remains after deactivation")
        from goblin_king.pipeline_kubernetes import restricted_job

        probe = "acceptance-off-data"
        script = (
            "import json,time; from pathlib import Path; "
            "p=Path('/data/activity.jsonl'); before=p.stat().st_size; time.sleep(4); "
            "after=p.stat().st_size; assert before==after; "
            "print(json.dumps({'activity_bytes':after, "
            "'sentinel':Path('/data/acceptance-sentinel').read_text()}))"
        )
        manifest = restricted_job(
            probe,
            namespace,
            self.proof.state["images"]["controller"]["digest"],
            ["python", "-c", script],
            volumes=[{"name": "data", "persistentVolumeClaim": {"claimName": "app-data"}}],
            mounts=[{"name": "data", "mountPath": "/data", "readOnly": True}],
            timeout=60,
        )
        self.proof.apply(manifest)
        self.proof.kube(
            "-n", namespace, "wait", "--for=condition=Complete", "job/" + probe, "--timeout=90s"
        )
        stopped_data = json.loads(self.proof.kube("-n", namespace, "logs", "job/" + probe))
        if stopped_data["sentinel"] != sentinel["sentinel"]:
            raise RuntimeError("deactivation lost the data challenge")
        build = self.build(self.merge("inactive-build", "python", "test"))
        inactive = self.release("python", "test")
        if (
            inactive["document"]["active"]
            or inactive["document"]["observed"]["build_id"] == build["id"]
        ):
            raise RuntimeError("new build activated an inactive release")
        on = self.operation(self.action("test-on", "python", "test", "activate")["operation_id"])
        after = self.deployed("python", "test", build["id"])
        old_health = json.loads(before["document"]["observed"]["health"]["body"])
        new_health = json.loads(after["document"]["observed"]["health"]["body"])
        if old_health["sentinel"] != new_health["sentinel"]:
            raise RuntimeError("persistent data sentinel changed")
        resumed_data = json.loads(
            self.proof.kube(
                "-n",
                namespace,
                "exec",
                "deploy/app",
                "--",
                "python",
                "-c",
                "import json,time; from pathlib import Path; time.sleep(4); "
                "print(json.dumps({'sentinel':Path('/data/acceptance-sentinel').read_text(), "
                "'activity_bytes':Path('/data/activity.jsonl').stat().st_size}))",
            )
        )
        if (
            resumed_data["sentinel"] != sentinel["sentinel"]
            or resumed_data["activity_bytes"] <= stopped_data["activity_bytes"]
        ):
            raise RuntimeError("activation lost data or did not resume background activity")
        return self.save(
            "test-off-on",
            {
                "off": off,
                "resources": resources,
                "inactive": inactive,
                "on": on,
                "after": after,
                "stopped_data": stopped_data,
                "resumed_data": resumed_data,
            },
        )

    def implementations(self):
        return {
            "fresh-installation": self.installation,
            "first-repository": self.first_repository,
            "reviewed-merges": self.reviewed_merges,
            "second-repository": self.second_repository,
            "invalid-input": self.invalid_input,
            "duplicate-reordered-events": self.duplicate_reordered,
            "missed-event": self.missed_event,
            "build-failures": self.build_failures,
            "failed-rollout": self.failed_rollout,
            "release-contention": self.release_contention,
            "interruptions": self.interruptions,
            "recording-recovery": self.recording_recovery,
            "test-off-on": self.test_off_on,
            "emergency-rollback": self.emergency_rollback,
            "identity-boundaries": self.identity_boundaries,
            "multiple-nodes": self.multiple_nodes,
            "storage-recovery": self.storage_recovery,
            "upgrade": self.upgrade,
            "side-effect-faults": self.side_effect_faults,
        }

    def side_effect_faults(self):
        from pipeline_fault_acceptance import FaultAcceptance

        return FaultAcceptance(self).run()

    def storage_recovery(self):
        from pipeline_storage_acceptance import prove_storage_recovery

        return prove_storage_recovery(self)

    def upgrade(self):
        from pipeline_upgrade_acceptance import prove_upgrade

        return prove_upgrade(self)

    def failed_rollout(self):
        previous = self.checkpoint("before-rollout-failure", lambda: self.release("node", "test"))
        build = self.build(self.merge("rollout-failure", "node", "test", fault="health"))
        if build["status"] != "published":
            raise RuntimeError("health fault did not reach deployment")
        operation = self.wait(
            "failed rollout and verified rollback record",
            lambda: next(
                (
                    op
                    for op in self.collection("operations")
                    if op["status"] == "failed"
                    and op["document"].get("execution", {}).get("build_id") == build["id"]
                    and op["document"].get("recording")
                ),
                None,
            ),
            timeout=900,
        )
        after = self.release("node", "test")
        observed = after["document"]["observed"]
        old = previous["document"]["observed"]
        if observed["image"] != old["image"] or observed["health"]["status"] != 200:
            raise RuntimeError("rollback did not independently verify the previous digest")
        if observed["helm_revision"] <= old["helm_revision"] or after["document"]["operation_id"]:
            raise RuntimeError("rollback did not settle with a new observed Helm revision")
        evidence = {
            "previous": previous,
            "failed_build": build,
            "operation": operation,
            "restored": after,
        }
        repair = self.build(self.merge("repair-rollout", "node", "test", fault="repair"))
        self.deployed("node", "test", repair["id"])
        return self.save("failed-rollout", evidence)

    def recording_recovery(self):
        self.request("/_fixture/recording", {"available": False}, provider=True)
        try:
            build = self.build(self.merge("recording-outage", "node", "test"))
            waiting = self.wait(
                "healthy rollout with recording failure",
                lambda: next(
                    (
                        op
                        for op in self.collection("operations")
                        if op["status"] == "recording"
                        and op["document"].get("record_error")
                        and op["document"].get("execution", {}).get("build_id") == build["id"]
                    ),
                    None,
                ),
                timeout=900,
            )
            before = self.release("node", "test")
            jobs_before = json.loads(
                self.proof.kube("-n", self.control, "get", "jobs", "-o", "json")
            )
        finally:
            self.request("/_fixture/recording", {"available": True}, provider=True)
        after = self.deployed("node", "test", build["id"])
        jobs_after = json.loads(self.proof.kube("-n", self.control, "get", "jobs", "-o", "json"))
        if after["document"]["observed"] != before["document"]["observed"]:
            raise RuntimeError("recording repair changed the observed release")
        if {j["metadata"]["uid"] for j in jobs_before["items"]} != {
            j["metadata"]["uid"] for j in jobs_after["items"]
        }:
            raise RuntimeError("recording repair created another execution")
        return self.save(
            "recording-recovery",
            {
                "waiting": waiting,
                "before": before,
                "after": after,
                "jobs_before": jobs_before,
                "jobs_after": jobs_after,
            },
        )

    def emergency_rollback(self):
        before = self.checkpoint("before-emergency", lambda: self.release("python", "test"))
        history = before["document"]["history"]
        selected = next(
            row
            for row in history
            if row["active"] and row["build_id"] != before["document"]["observed"]["build_id"]
        )
        operation = self.operation(
            self.action(
                "emergency", "python", "test", "rollback", {"build_id": selected["build_id"]}
            )["operation_id"]
        )
        restored = self.release("python", "test")
        if (
            operation["status"] != "completed"
            or restored["document"]["observed"]["image"] != selected["image"]
        ):
            raise RuntimeError("emergency rollback did not restore a known healthy digest")
        if restored["document"]["hold"]["reason"] != "emergency rollback":
            raise RuntimeError("emergency rollback did not retain a hold")
        build = self.build(self.merge("held-build", "python", "test"))
        time.sleep(12)
        held = self.release("python", "test")
        if (
            held["document"]["observed"] != restored["document"]["observed"]
            or not held["document"]["hold"]
        ):
            raise RuntimeError("new build bypassed the emergency hold")
        cleared = self.operation(
            self.action("clear-emergency", "python", "test", "clear-hold")["operation_id"]
        )
        final = self.deployed("python", "test", build["id"])
        return self.save(
            "emergency-rollback",
            {"operation": operation, "held": held, "clear": cleared, "final": final},
        )

    def multiple_nodes(self):
        executions = []
        for implementation in ("python", "node"):
            namespace = self.prefix + "-" + implementation + "-build"
            pods = json.loads(self.proof.kube("-n", namespace, "get", "pods", "-o", "json"))[
                "items"
            ]
            for pod in pods:
                if pod["status"]["phase"] != "Succeeded":
                    continue
                if any(
                    v.get("persistentVolumeClaim", {}).get("claimName") == "delivery-data"
                    for v in pod["spec"].get("volumes", [])
                ):
                    raise RuntimeError("source execution mounted control-plane storage")
                executions.append(
                    {
                        "namespace": namespace,
                        "name": pod["metadata"]["name"],
                        "uid": pod["metadata"]["uid"],
                        "node": pod["spec"]["nodeName"],
                    }
                )
        if len({item["node"] for item in executions}) < 2:
            raise RuntimeError("successful source execution was not observed on both nodes")
        return self.save("multiple-nodes", executions)

    def signed_event(self, delivery, *, event=None, invalid_signature=False, identity=None):
        script = """
import hashlib, hmac, json, sys
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError
args=json.load(sys.stdin)
raw=json.dumps(args['event'], separators=(',', ':')).encode()
secret=Path('/data/webhook-secret').read_bytes()
signature='sha256=' + hmac.new(secret, raw, hashlib.sha256).hexdigest()
if args['invalid_signature']: signature='sha256=' + '0'*64
req=Request(args['url'], data=raw, headers={'Content-Type':'application/json',
    'X-Hub-Signature-256':signature, 'X-GitHub-Delivery':args['id'], 'X-GitHub-Event':'push'})
try:
    with urlopen(req, timeout=30) as response:
        print(json.dumps({'status':response.status,'body':json.load(response)}))
except HTTPError as error:
    print(json.dumps({'status':error.code,'body':json.load(error)}))
"""
        return json.loads(
            self.proof.kube(
                "-n",
                self.provider,
                "exec",
                "-i",
                "deploy/provider",
                "--",
                "python",
                "-c",
                script,
                document={
                    "url": f"http://delivery-api.{self.control}.svc:8000/pipeline/webhooks/github",
                    "event": event or delivery["event"],
                    "id": identity or delivery["id"],
                    "invalid_signature": invalid_signature,
                },
            )
        )

    def invalid_input(self):
        before = {row["id"] for row in self.collection("builds")}
        delivery = self.merge("unreviewed", "python", "test", unreviewed=True, webhook=False)
        evidence = {"unreviewed": delivery}
        event = delivery["event"]
        cases = {
            "forged": (event, True),
            "installation": (event | {"installation": {"id": 999}}, False),
            "repository": (
                event | {"repository": {"id": 999, "full_name": "fixture/unknown"}},
                False,
            ),
            "branch": (event | {"ref": "refs/heads/unknown"}, False),
            "unreviewed": (event, False),
        }
        for name, (body, forged) in cases.items():
            result = self.signed_event(
                delivery, event=body, invalid_signature=forged, identity="invalid-" + name
            )
            if name in {"forged", "installation"} and result["status"] not in {400, 401, 403, 422}:
                raise RuntimeError("invalid authentication was accepted: " + name)
            evidence[name] = result
        time.sleep(15)
        if {row["id"] for row in self.collection("builds")} != before:
            raise RuntimeError("unapproved input created executable work")
        invalid = self.merge("invalid-contract", "python", "test", fault="contract")
        time.sleep(15)
        if {row["id"] for row in self.collection("builds")} != before:
            raise RuntimeError("invalid contract created executable work")
        evidence["contract"] = invalid
        repair = self.build(self.merge("repair-invalid", "python", "test", fault="repair"))
        self.deployed("python", "test", repair["id"])
        return self.save("invalid-input", evidence)

    def duplicate_reordered(self):
        first = self.merge("ordered-one", "python", "test", webhook=False)
        second = self.merge("ordered-two", "python", "test", webhook=False)
        received = [self.signed_event(second), self.signed_event(second), self.signed_event(first)]
        conflict = self.signed_event(first, identity=second["id"])
        if conflict["status"] != 409:
            raise RuntimeError("conflicting delivery ID was accepted")
        build = self.build(second)
        final = self.deployed("python", "test", build["id"])
        time.sleep(12)
        builds = [
            row
            for row in self.collection("builds")
            if row["document"]["request"]["source"]["commit"] == second["event"]["after"]
        ]
        if (
            len(builds) != 1
            or self.release("python", "test")["document"]["desired_commit"]
            != second["event"]["after"]
        ):
            raise RuntimeError("duplicate or old event changed desired state")
        from pipeline_supersession_acceptance import prove_supersession

        inflight = prove_supersession(self)
        return self.save(
            "duplicate-reordered-events",
            {
                "receipts": received,
                "conflict": conflict,
                "builds": builds,
                "final": final,
                "inflight_supersession": inflight,
            },
        )

    def release_contention(self):
        first = self.action("contention-off", "python", "test", "deactivate")
        row = self.release("python", "test")
        denied = []
        for action in ("activate", "rollback"):
            denied.append(
                self.request(
                    f"/pipeline/releases/{row['key']}/operations",
                    {
                        "expected_version": row["version"],
                        "action": action,
                        "idempotency_key": "contending-" + action,
                        "payload": {"build_id": row["document"]["observed"]["build_id"]},
                    },
                    expected=(409,),
                )
            )
        settled = self.operation(first["operation_id"])
        if settled["status"] != "completed":
            raise RuntimeError("exclusive operation failed")
        active = self.operation(
            self.action("contention-on", "python", "test", "activate")["operation_id"]
        )
        return self.save(
            "release-contention", {"denied": denied, "first": settled, "active": active}
        )

    def identity_boundaries(self):
        from goblin_king.pipeline_kubernetes import restricted_job

        image = self.proof.state["images"]["controller"]["digest"]
        namespace = self.prefix + "-python-build"
        targets = {
            "control_api": (f"delivery-api.{self.control}.svc", 8000),
            "redis": (f"delivery-redis.{self.control}.svc", 6379),
            "kubernetes": ("kubernetes.default.svc", 443),
            "production_data": (f"app.{self.prefix}-python-prod.svc", 8080),
            "trusted_provider": (f"provider.{self.provider}.svc", 8770),
        }
        script = """
import json, socket
from pathlib import Path
results={}
for name,(host,port) in TARGETS.items():
    socket.getaddrinfo(host,port)
    try:
        with socket.create_connection((host,port),timeout=3): pass
        results[name]='reachable'
    except (OSError,TimeoutError): results[name]='denied'
token=Path('/var/run/secrets/kubernetes.io/serviceaccount/token')
results['service_account_token']='present' if token.exists() else 'absent'
results['control_database']='present' if Path('/data/goblin-king.sqlite3').exists() else 'absent'
print(json.dumps(results),flush=True)
assert all(results[k]=='denied' for k in TARGETS)
assert results['service_account_token']==results['control_database']=='absent'
""".replace("TARGETS", repr(targets))
        name = "source-identity-proof"
        manifest = restricted_job(
            name, namespace, image, ["python", "-c", script], trust="source-test", timeout=90
        )
        self.proof.apply(manifest)
        self.proof.kube(
            "-n", namespace, "wait", "--for=condition=Complete", "job/" + name, "--timeout=110s"
        )
        denied = json.loads(self.proof.kube("-n", namespace, "logs", "job/" + name))
        script = """
import json,ssl
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.error import HTTPError
root=Path('/var/run/secrets/kubernetes.io/serviceaccount')
context=ssl.create_default_context(cafile=str(root/'ca.crt'))
results={}
for name,path in PATHS.items():
    req=Request('https://kubernetes.default.svc'+path,
        headers={'Authorization':'Bearer '+(root/'token').read_text()})
    try:
        with urlopen(req,context=context,timeout=10) as response: results[name]=response.status
    except HTTPError as error: results[name]=error.code
print(json.dumps(results),flush=True)
assert all(code==403 for code in results.values())
""".replace(
            "PATHS",
            repr(
                {
                    "application_secrets": f"/api/v1/namespaces/{self.prefix}-python-test/secrets",
                    "production_deployments": (
                        f"/apis/apps/v1/namespaces/{self.prefix}-python-prod/deployments"
                    ),
                    "control_storage": f"/api/v1/namespaces/{self.control}/persistentvolumeclaims",
                }
            ),
        )
        name = "deployment-identity-proof"
        manifest = restricted_job(name, self.control, image, ["python", "-c", script], timeout=90)
        pod = manifest["spec"]["template"]["spec"]
        pod["serviceAccountName"] = "pipeline-deploy-test"
        pod["automountServiceAccountToken"] = True
        self.proof.apply(manifest)
        self.proof.kube(
            "-n", self.control, "wait", "--for=condition=Complete", "job/" + name, "--timeout=110s"
        )
        authority = json.loads(self.proof.kube("-n", self.control, "logs", "job/" + name))
        # Use the existing scoped publication Secret without exposing its values.
        script = """
import json
from pathlib import Path
from goblin_king.pipeline_oci import RegistryClient
root=Path('/auth')
client=RegistryClient('https://registry.gk-local-registry.svc',(root/'username').read_text(),(root/'password').read_text(),root/'ca.crt')
status,_,_=client.request('/v2/node/forbidden/blobs/uploads/',method='POST',data=b'',expected=(401,403))
print(json.dumps({'other_repository_status':status}))
"""
        name = "publication-identity-proof"
        manifest = restricted_job(
            name,
            namespace,
            image,
            ["python", "-c", script],
            volumes=[{"name": "auth", "secret": {"secretName": "registry-publisher"}}],
            mounts=[{"name": "auth", "mountPath": "/auth", "readOnly": True}],
            timeout=90,
        )
        self.proof.apply(manifest)
        self.proof.kube(
            "-n", namespace, "wait", "--for=condition=Complete", "job/" + name, "--timeout=110s"
        )
        registry = json.loads(self.proof.kube("-n", namespace, "logs", "job/" + name))
        return self.save(
            "identity-boundaries", {"source": denied, "deployment": authority, "registry": registry}
        )

    def interruptions(self):
        delivery = self.merge("restart-build", "node", "main")
        commit = delivery["event"]["after"]
        running = self.wait(
            "running build before restart",
            lambda: next(
                (
                    row
                    for row in self.collection("builds")
                    if row["document"]["request"]["source"]["commit"] == commit
                ),
                None,
            ),
        )
        from uuid import NAMESPACE_URL, uuid5

        job_id = str(uuid5(NAMESPACE_URL, f"goblin-king/pipeline/{running['id']}/build/1"))
        execution = str(uuid5(NAMESPACE_URL, "goblin-king/execution/" + job_id))
        job_name = "gk-pipeline-build-" + execution
        before_job = self.wait(
            "retained running execution",
            lambda: json.loads(
                self.proof.kube(
                    "-n", self.control, "get", "job", job_name, "--ignore-not-found", "-o", "json"
                )
                or "null"
            ),
        )
        self.checkpoint(
            "controller-scheduler-restart",
            lambda: {
                "command": self.proof.kube(
                    "-n",
                    self.control,
                    "rollout",
                    "restart",
                    "deployment/delivery-api",
                    "deployment/delivery-scheduler",
                ),
                "job_uid": before_job["metadata"]["uid"],
            },
        )
        for component in ("api", "scheduler"):
            self.proof.kube(
                "-n",
                self.control,
                "rollout",
                "status",
                "deployment/delivery-" + component,
                "--timeout=90s",
            )
        built = self.build(delivery)
        after_job = json.loads(
            self.proof.kube("-n", self.control, "get", "job", job_name, "-o", "json")
        )
        if before_job["metadata"]["uid"] != after_job["metadata"]["uid"]:
            raise RuntimeError("scheduler restart replaced the execution Job")
        normal = self.deployed("node", "prod", built["id"])
        # A bounded unhealthy rollout gives a deterministic interruption window.
        failed = self.build(self.merge("interrupted-rollout", "node", "test", fault="health"))
        operation = self.wait(
            "claimed rollout to interrupt",
            lambda: next(
                (
                    row
                    for row in self.collection("operations")
                    if row["status"] == "running"
                    and row["document"]["execution"]["build_id"] == failed["id"]
                ),
                None,
            ),
            timeout=900,
        )
        execution = str(
            uuid5(NAMESPACE_URL, "goblin-king/execution/" + operation["document"]["job_id"])
        )
        ledger = json.loads(
            self.proof.kube(
                "-n", self.control, "get", "configmap", "pipeline-" + execution, "-o", "json"
            )
        )
        stage = json.loads(ledger["data"]["execution.json"])["stages"]["worker"]
        pods = json.loads(
            self.proof.kube(
                "-n", self.control, "get", "pods", "-l", "job-name=" + stage["name"], "-o", "json"
            )
        )["items"]
        if len(pods) != 1:
            raise RuntimeError("interruption requires one authoritative execution Pod")
        pod = pods[0]
        patch = [
            {"op": "test", "path": "/metadata/uid", "value": pod["metadata"]["uid"]},
            {"op": "add", "path": "/spec/activeDeadlineSeconds", "value": 1},
        ]
        self.proof.kube(
            "-n",
            self.control,
            "patch",
            "pod",
            pod["metadata"]["name"],
            "--type=json",
            "-p",
            json.dumps(patch),
        )
        held = self.operation(operation["id"])
        if held["status"] != "held":
            raise RuntimeError("interrupted release did not retain a recovery hold")
        release = self.release("node", "test")
        denied = self.request(
            f"/pipeline/releases/{release['key']}/operations",
            {
                "action": "activate",
                "expected_version": release["version"],
                "idempotency_key": "no-steal-interrupted",
            },
            expected=(409,),
        )
        recovery = self.request(
            f"/pipeline/operations/{held['id']}/recover",
            {"expected_version": held["version"], "idempotency_key": "reconcile-interrupted"},
        )
        recovered = self.operation(held["id"])
        if recovered["status"] not in {"completed", "failed"}:
            raise RuntimeError("terminated execution could not reconcile actual release state")
        restored = self.release("node", "test")
        if (
            restored["document"]["hold"]["reason"]
            != "recovered release awaiting explicit hold clearance"
        ):
            raise RuntimeError("recovery did not retain its explicit clearance hold")
        repair = self.build(self.merge("repair-interrupted", "node", "test", fault="repair"))
        cleared = self.operation(
            self.action("clear-recovery", "node", "test", "clear-hold")["operation_id"]
        )
        final = self.deployed("node", "test", repair["id"])
        return self.save(
            "interruptions",
            {
                "original_job": before_job,
                "reattached_job": after_job,
                "normal_release": normal,
                "interrupted": held,
                "contention": denied,
                "recovery": recovery,
                "reconciled": recovered,
                "clear": cleared,
                "final": final,
            },
        )
