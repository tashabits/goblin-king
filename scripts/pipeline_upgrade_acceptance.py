"""Upgrade the original foundation chart and persisted data on the real cluster."""

import io
import json
import subprocess
import tarfile
import time

FOUNDATION = "be2192081291c005e1879ab9486451479152e784"


def prove_upgrade(delivery):
    proof = delivery.proof
    namespace = proof.namespace("upgrade")
    output = delivery.output / "upgrade"
    output.mkdir(parents=True, exist_ok=True)
    inspected = json.loads(
        proof.command(
            [
                "docker",
                "image",
                "inspect",
                "goblin-king:pipeline-foundation-" + FOUNDATION[:12],
            ]
        )
    )[0]
    foundation_image = inspected["RepoDigests"][0]
    if (inspected["Config"].get("Labels") or {}).get(
        "org.opencontainers.image.revision"
    ) != FOUNDATION:
        raise RuntimeError("foundation image is not tied to the required source revision")
    proof.apply(
        {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": {"name": "foundation-image-cache", "namespace": namespace},
            "spec": {
                "nodeSelector": {"kubernetes.io/hostname": "desktop-worker"},
                "automountServiceAccountToken": False,
                "containers": [
                    {
                        "name": "cache",
                        "image": inspected["RepoTags"][0],
                        "imagePullPolicy": "Always",
                        "command": ["python", "-c", "import time; time.sleep(3600)"],
                        "resources": {
                            "requests": {"cpu": "5m", "memory": "16Mi"},
                            "limits": {"cpu": "100m", "memory": "64Mi"},
                        },
                    }
                ],
            },
        }
    )
    proof.kube(
        "-n",
        namespace,
        "wait",
        "--for=condition=Ready",
        "pod/foundation-image-cache",
        "--timeout=120s",
    )
    cached = json.loads(
        proof.kube("-n", namespace, "get", "pod/foundation-image-cache", "-o", "json")
    )
    if not cached["status"]["containerStatuses"][0]["imageID"].endswith(
        foundation_image.split("@")[1]
    ):
        raise RuntimeError("foundation image differs from the expected digest")
    raw = subprocess.check_output(["git", "archive", FOUNDATION, "charts/goblin-king"], timeout=30)
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        archive.extractall(output, filter="data")
    source_values = json.loads((delivery.output / "values.json").read_text())
    # Secrets are copied through captured memory and stdin, never through proof logs.
    for name in ("pipeline-auth", "pipeline-github-app", "registry-reader"):
        record = json.loads(
            subprocess.check_output(
                [
                    "kubectl",
                    "--context",
                    proof.state["context"],
                    "-n",
                    delivery.control,
                    "get",
                    "secret",
                    name,
                    "-o",
                    "json",
                ],
                text=True,
                encoding="utf-8",
                timeout=30,
            )
        )
        proof.apply(
            {
                "apiVersion": "v1",
                "kind": "Secret",
                "metadata": {"name": name, "namespace": namespace},
                "type": record.get("type", "Opaque"),
                "data": record["data"],
            }
        )
    values = {
        "fullnameOverride": "foundation",
        "image": {
            "repository": foundation_image.split("@")[0],
            "digest": foundation_image.split("@")[1],
        },
        "persistence": {
            "enabled": True,
            "topology": "sqlite-single-node",
            "sqliteNode": "desktop-worker",
        },
        "api": {
            "resources": {
                "requests": {"cpu": "50m", "memory": "128Mi"},
                "limits": {"cpu": "1", "memory": "512Mi"},
            }
        },
        "scheduler": {"enabled": False, "workloadSecurity": {"profile": "restricted-v1"}},
        "admin": {"enabled": False},
        "repository": {"enabled": False},
        "workers": {"exampleLongHello": {"enabled": False}},
    }

    def install(chart, installed_values):
        path = output / "install-values.json"
        path.write_text(json.dumps(installed_values, indent=2))
        return proof.command(
            [
                "helm",
                "--kube-context",
                proof.state["context"],
                "-n",
                namespace,
                "upgrade",
                "--install",
                "foundation",
                str(chart),
                "-f",
                str(path),
                "--wait=watcher",
                "--timeout=180s",
            ],
            timeout=240,
        )

    def execute(script):
        return json.loads(
            proof.kube(
                "-n",
                namespace,
                "exec",
                "-i",
                "deploy/foundation-api",
                "--",
                "python",
                "-c",
                "import json,sys; exec(json.load(sys.stdin)['script'])",
                document={"script": script},
            )
        )

    def seed():
        install(output / "charts/goblin-king", values)
        return execute("""
import json
from pathlib import Path
from examples.pipeline.persistence_probe import probe
from goblin_king.store import SQLiteStore
from goblin_king.contracts import JobRecord,utc_now
path=Path('/data/goblin-king.sqlite3')
admission=probe(path)
store=SQLiteStore(path)
job=JobRecord(id='historical-completed-job',kind='example.hello',input={'message':'preserved'},
    status='completed',created_at=utc_now())
store.save_job(job)
print(json.dumps({'admission':admission,'job':job.model_dump(mode='json')}))
""")

    seeded = delivery.checkpoint("foundation-seeded", seed)
    values["image"] = {
        "repository": proof.state["images"]["controller"]["digest"].split("@")[0],
        "digest": proof.state["images"]["controller"]["digest"].split("@")[1],
    }
    values["pipeline"] = {
        "enabled": True,
        "authSecret": "pipeline-auth",
        "githubSecret": "pipeline-github-app",
        "registryReaderSecret": "registry-reader",
        "settings": source_values["pipeline"]["settings"]
        | {
            "release_repository": "",
            "release_repository_id": 0,
            "deploy_image": "",
            "recovery_mode": False,
        },
    }
    from pipeline_acceptance import ROOT

    install(ROOT / "charts/goblin-king", values)
    # Startup and a second reconciliation must leave the historical admission dormant.
    time.sleep(12)
    after = execute("""
import json
from pathlib import Path
from sqlalchemy import select,func
from goblin_king.store import SQLiteStore
from goblin_king.pipeline_store import PipelineAdmissionStore
from goblin_king.store_schema import pipeline_dispatch_table,pipeline_builds_table
store=SQLiteStore(Path('/data/goblin-king.sqlite3'))
with store.engine.connect() as connection:
    counts={table.name:connection.scalar(select(func.count()).select_from(table))
        for table in (pipeline_dispatch_table,pipeline_builds_table)}
requests=PipelineAdmissionStore(store).list_requests(project_id='sample-project')
print(json.dumps({'counts':counts,'jobs':[j.model_dump(mode='json') for j in store.list_jobs()],
    'requests':[r.model_dump(mode='json') for r in requests]}))
""")
    if (
        after["jobs"] != [seeded["job"]]
        or any(after["counts"].values())
        or len(after["requests"]) != 1
        or after["requests"][0]["build_id"] != seeded["admission"]["build_id"]
    ):
        raise RuntimeError("upgrade changed historical work or dispatched an old admission")
    pods = json.loads(proof.kube("-n", namespace, "get", "pods", "-o", "json"))
    jobs = json.loads(proof.kube("-n", namespace, "get", "jobs", "-o", "json"))
    if jobs["items"]:
        raise RuntimeError("upgrade unexpectedly created execution Jobs")
    return delivery.save(
        "upgrade",
        {
            "foundation_revision": FOUNDATION,
            "foundation_image": foundation_image,
            "before": seeded,
            "after": after,
            "pods": pods,
            "jobs": jobs,
        },
    )
