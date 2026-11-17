"""Prove bounded node loss and fenced restoration on the local kind cluster."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from pipeline_storage_recovery import StorageRecovery


def watchdog(container_id, seconds):
    """Resume only the exact paused container if the parent process disappears."""
    time.sleep(seconds)
    inspected = json.loads(subprocess.check_output(["docker", "inspect", container_id]))[0]
    if inspected["Id"] == container_id and inspected["State"]["Paused"]:
        subprocess.run(["docker", "unpause", container_id], check=True, capture_output=True)


def node_outage(delivery):
    proof = delivery.proof
    recovery = StorageRecovery(
        proof.state["context"], delivery.control, "delivery", delivery.output / "storage"
    )
    values = recovery.values()
    source = values["persistence"]["sqliteNode"]
    node = json.loads(proof.kube("get", "node", source, "-o", "json"))
    inspected = json.loads(proof.command(["docker", "inspect", source]))[0]
    labels = inspected["Config"].get("Labels", {})
    addresses = {
        entry["address"] for entry in node["status"]["addresses"] if entry["type"] == "InternalIP"
    }
    container_addresses = {
        entry["IPAddress"] for entry in inspected["NetworkSettings"]["Networks"].values()
    }
    if (
        labels.get("io.x-k8s.kind.cluster") != "desktop"
        or labels.get("io.x-k8s.kind.role") != "worker"
        or not addresses.intersection(container_addresses)
        or not inspected["State"]["Running"]
        or inspected["State"]["Paused"]
    ):
        raise RuntimeError("node outage requires the identified, running local kind worker")

    def jobs():
        return json.loads(proof.kube("-n", delivery.control, "get", "jobs", "-o", "json"))["items"]

    def ready():
        current = json.loads(proof.kube("get", "node", source, "-o", "json"))
        if current["metadata"]["uid"] != node["metadata"]["uid"]:
            raise RuntimeError("node identity changed during outage")
        return current, next(
            item["status"] for item in current["status"]["conditions"] if item["type"] == "Ready"
        )

    before = jobs()
    if any(
        not (job.get("status", {}).get("succeeded") or job.get("status", {}).get("failed"))
        for job in before
    ):
        raise RuntimeError("settle delivery executions before the storage outage")
    deployments = json.loads(
        proof.kube("-n", delivery.control, "get", "deployments", "-o", "json")
    )["items"]
    writers = [
        item
        for item in deployments
        if item["metadata"]["name"] in {"delivery-api", "delivery-scheduler"}
    ]
    if len(writers) != 2 or any(
        item["spec"]["template"]["spec"].get("nodeSelector", {}).get("kubernetes.io/hostname")
        != source
        or item["spec"].get("replicas") != 1
        for item in writers
    ):
        raise RuntimeError("outage requires both active SQLite services pinned to the source node")
    container_id = inspected["Id"]
    caches = json.loads(
        proof.kube(
            "-n", delivery.control, "get", "pods", "-l", "app=pipeline-image-cache", "-o", "json"
        )
    )["items"]
    observer = next(pod for pod in caches if pod["spec"]["nodeName"] != source)

    def api_health():
        script = """
import json,sys
from urllib.request import urlopen
try:
    with urlopen(json.load(sys.stdin)['url'],timeout=3) as response:
        print(json.dumps({'available':response.status==200,'status':response.status}))
except Exception as error:
    print(json.dumps({'available':False,'error':type(error).__name__}))
"""
        return json.loads(
            proof.kube(
                "-n",
                delivery.control,
                "exec",
                "-i",
                observer["metadata"]["name"],
                "-c",
                "controller",
                "--",
                "python",
                "-c",
                script,
                document={"url": f"http://delivery-api.{delivery.control}.svc:8000/health"},
            )
        )

    health_before = api_health()
    if not health_before["available"]:
        raise RuntimeError("outage observer cannot reach the healthy API before interruption")
    guard = subprocess.Popen(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "watchdog",
            "--container",
            container_id,
            "--seconds",
            "150",
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    evidence = {
        "node": source,
        "node_uid": node["metadata"]["uid"],
        "container_id": container_id,
        "before_jobs": [item["metadata"]["uid"] for item in before],
        "health_before": health_before,
    }
    try:
        proof.command(["docker", "pause", container_id])
        deadline = time.monotonic() + 110
        while True:
            current, condition = ready()
            if condition != "True":
                evidence["unavailable_node"] = current
                break
            if time.monotonic() >= deadline:
                raise RuntimeError("paused node never became unavailable")
            time.sleep(3)
        during = jobs()
        if {item["metadata"]["uid"] for item in during} != set(evidence["before_jobs"]):
            raise RuntimeError("delivery dispatched new execution during source-node loss")
        pods = json.loads(proof.kube("-n", delivery.control, "get", "pods", "-o", "json"))["items"]
        writer_pods = [
            pod
            for pod in pods
            if pod["metadata"].get("labels", {}).get("app.kubernetes.io/component")
            in {"api", "scheduler"}
        ]
        if len(writer_pods) != 2 or any(
            pod["spec"].get("nodeName") != source for pod in writer_pods
        ):
            raise RuntimeError("SQLite writer moved to another node during loss")
        evidence["held_writer_pods"] = writer_pods
        evidence["during_jobs"] = [item["metadata"]["uid"] for item in during]
        evidence["health_during"] = api_health()
        if evidence["health_during"]["available"]:
            raise RuntimeError(
                "delivery API remained available after its sole storage node stopped"
            )
    finally:
        state = json.loads(proof.command(["docker", "inspect", container_id]))[0]
        if state["State"]["Paused"]:
            proof.command(["docker", "unpause", container_id])
        guard.terminate()
        guard.wait(timeout=10)
    deadline = time.monotonic() + 150
    while ready()[1] != "True":
        if time.monotonic() >= deadline:
            raise RuntimeError("node did not recover after unpause")
        time.sleep(3)
    for item in writers:
        proof.kube(
            "-n",
            delivery.control,
            "rollout",
            "status",
            "deployment/" + item["metadata"]["name"],
            "--timeout=90s",
        )
    evidence["recovered_node"] = ready()[0]
    evidence["health_recovery_attempts"] = []
    deadline = time.monotonic() + 90
    while True:
        observed = api_health()
        evidence["health_recovery_attempts"].append(observed)
        if observed["available"]:
            evidence["health_after"] = observed
            break
        if time.monotonic() >= deadline:
            delivery.save("storage-outage", evidence)
            raise RuntimeError("delivery API route did not recover after the node resumed")
        time.sleep(3)
    delivery.save("storage-outage", evidence)
    return evidence


def prove_storage_recovery(delivery):
    proof = delivery.proof
    output = delivery.output / "storage"
    recovery = StorageRecovery(proof.state["context"], delivery.control, "delivery", output)

    def inventory():
        result = recovery.inventory()
        if "registrations" not in result:
            result["registrations"] = delivery.collection("repositories")
        return result

    before = delivery.checkpoint("storage-before", inventory)
    if any(
        row["status"] not in {"completed", "failed", "superseded"} for row in before["operations"]
    ):
        raise RuntimeError("settle release operations before storage recovery")
    if any(row["status"] in {"admitted", "held"} for row in before["builds"]):
        raise RuntimeError("settle build executions before storage recovery")
    outage = delivery.checkpoint("storage-outage", lambda: node_outage(delivery))
    backup = delivery.checkpoint("storage-backup", recovery.backup)
    artifacts = delivery.checkpoint("storage-artifacts", recovery.artifacts)
    metadata = json.loads((output / "backup.json").read_text())
    destinations = [
        node["metadata"]["name"]
        for node in proof.state["nodes"]["items"]
        if node["metadata"]["name"] != metadata["source_node"]
    ]
    if not destinations:
        raise RuntimeError("restoration requires another node")
    restored = delivery.checkpoint("storage-restore", lambda: recovery.restore(destinations[0]))
    after = inventory()
    if not after["recovery_mode"]:
        raise RuntimeError("restored state did not hold delivery")
    for collection in ("registrations", "builds", "releases", "operations"):
        if collection not in before or before[collection] != after[collection]:
            raise RuntimeError("restoration changed durable " + collection)
    reconciled = delivery.checkpoint("storage-reconcile", lambda: recovery.reconcile(enable=True))
    original = json.loads(
        proof.kube("-n", delivery.control, "get", "pvc", metadata["source_claim"], "-o", "json")
    )
    if original["metadata"]["uid"] != metadata["source_claim_uid"]:
        raise RuntimeError("source claim was not preserved")
    return delivery.save(
        "storage-recovery",
        {
            "outage": outage,
            "backup": backup,
            "artifacts": artifacts,
            "restore": restored,
            "reconciled": reconciled,
            "source_claim_uid": original["metadata"]["uid"],
            "before": before,
            "after": after,
        },
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("watchdog",))
    parser.add_argument("--container", required=True)
    parser.add_argument("--seconds", type=int, default=150)
    args = parser.parse_args()
    watchdog(args.container, args.seconds)
