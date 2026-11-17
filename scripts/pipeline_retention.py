"""Export acknowledged execution evidence before pruning terminated Jobs and scratch claims."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from kubernetes import client, config
from kubernetes.client import ApiException


def eligible(row, ledger, now, hours):
    document = row["document"]
    if row["status"] not in {"published", "superseded", "failed", "completed"}:
        return False
    if not document.get("execution_acknowledged") or not ledger.get("acknowledged"):
        return False
    if (
        ledger.get("identity", {}).get("job_id") != document.get("job_id")
        or ledger.get("result") != document.get("result")
        or not ledger.get("acknowledged_at")
    ):
        return False
    acknowledged = datetime.fromisoformat(ledger["acknowledged_at"])
    return acknowledged <= now - timedelta(hours=hours)


def prune(context, namespace, release, output, *, hours=168, apply=False):
    from pipeline_storage_recovery import StorageRecovery

    if hours < 1:
        raise ValueError("execution evidence must remain available for at least one hour")
    output.mkdir(parents=True, exist_ok=True)
    recovery = StorageRecovery(context, namespace, release, output)
    inventory = recovery.inventory()
    config.load_kube_config(context=context)
    core, batch = client.CoreV1Api(), client.BatchV1Api()
    api_client = client.ApiClient()
    evidence = []

    def read_record(name, target):
        record = core.read_namespaced_config_map(name, target)
        return record, json.loads(record.data["execution.json"])

    def terminated_job(stage, target):
        try:
            job = batch.read_namespaced_job(stage["name"], target)
        except ApiException as error:
            if error.status == 404:
                return None
            raise
        if job.metadata.uid != stage["uid"] or not (job.status.succeeded or job.status.failed):
            raise RuntimeError("retention encountered a replaced or nonterminal Job")
        pods = core.list_namespaced_pod(target, label_selector="job-name=" + stage["name"]).items
        for pod in pods:
            statuses = (pod.status.container_statuses or []) + (
                pod.status.init_container_statuses or []
            )
            if (
                not any(ref.uid == job.metadata.uid for ref in pod.metadata.owner_references or [])
                or pod.status.phase not in {"Succeeded", "Failed"}
                or len(statuses) != len(pod.spec.containers) + len(pod.spec.init_containers or [])
                or any(item.state.terminated is None for item in statuses)
            ):
                raise RuntimeError("retention encountered an unproven Pod termination")
        return job

    for row in inventory["builds"] + inventory["operations"]:
        job_id = row["document"].get("job_id")
        if (
            not job_id
            or not row["document"].get("execution_acknowledged")
            or row["status"] not in {"published", "superseded", "failed", "completed"}
        ):
            continue
        name = "pipeline-" + str(uuid5(NAMESPACE_URL, "goblin-king/execution/" + job_id))
        record, ledger = read_record(name, namespace)
        if not eligible(row, ledger, datetime.now(UTC), hours):
            continue
        ledgers = [(namespace, record, ledger)]
        source = ledger.get("source_namespace")
        if source:
            source_record, source_ledger = read_record("pipeline-" + row["id"], source)
            if (
                source_ledger.get("identity", {})
                .get("request", {})
                .get("request", {})
                .get("build_id")
                != row["id"]
            ):
                raise RuntimeError("source evidence belongs to another build")
            if source_ledger.get("result") is None:
                continue
            ledgers.append((source, source_record, source_ledger))
        selected = []
        for target, _, retained in ledgers:
            for stage in retained["stages"].values():
                if stage["status"] not in {"complete", "removed"}:
                    raise RuntimeError("retention cannot remove an unsettled execution")
                if stage["status"] == "removed":
                    continue
                job = terminated_job(stage, target)
                if job:
                    selected.append(("job", target, job))
            for scratch in retained.get("scratch", {}).values():
                try:
                    claim = core.read_namespaced_persistent_volume_claim(scratch["name"], target)
                except ApiException as error:
                    if error.status == 404:
                        continue
                    raise
                if (
                    claim.metadata.uid != scratch["uid"]
                    or claim.metadata.labels.get("goblin-king.io/build") != row["id"]
                ):
                    raise RuntimeError("scratch claim identity changed")
                for pod in core.list_namespaced_pod(target).items:
                    if pod.status.phase not in {"Succeeded", "Failed"} and any(
                        volume.persistent_volume_claim
                        and volume.persistent_volume_claim.claim_name == claim.metadata.name
                        for volume in pod.spec.volumes or []
                    ):
                        raise RuntimeError("scratch claim remains mounted by an active Pod")
                selected.append(("pvc", target, claim))
        snapshot = {
            "row": row,
            "ledgers": [api_client.sanitize_for_serialization(entry[1]) for entry in ledgers],
            "resources": [
                {
                    "kind": kind,
                    "namespace": target,
                    "resource": api_client.sanitize_for_serialization(resource),
                }
                for kind, target, resource in selected
            ],
        }
        raw = json.dumps(snapshot, sort_keys=True, indent=2).encode()
        digest = hashlib.sha256(raw).hexdigest()
        path = output / (row["id"] + "-" + digest[:12] + ".json")
        path.write_bytes(raw)
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise RuntimeError("retention export verification failed")
        if apply:
            current = recovery.inventory()
            current_row = next(
                item
                for item in current["builds"] + current["operations"]
                if item["id"] == row["id"]
            )
            if current_row != row:
                raise RuntimeError("durable execution changed after retention preview")
            for target, retained_record, _ in ledgers:
                latest = core.read_namespaced_config_map(retained_record.metadata.name, target)
                if latest.metadata.resource_version != retained_record.metadata.resource_version:
                    raise RuntimeError("retained execution changed after export")
            for kind, target, resource in selected:
                options = client.V1DeleteOptions(
                    preconditions=client.V1Preconditions(
                        uid=resource.metadata.uid,
                        resource_version=resource.metadata.resource_version,
                    ),
                    propagation_policy="Foreground",
                )
                if kind == "job":
                    batch.delete_namespaced_job(resource.metadata.name, target, body=options)
                else:
                    core.delete_namespaced_persistent_volume_claim(
                        resource.metadata.name, target, body=options
                    )
        evidence.append(
            {
                "id": row["id"],
                "export": str(path),
                "sha256": digest,
                "resources": len(selected),
                "deleted": apply,
            }
        )
    (output / "retention.json").write_text(json.dumps(evidence, indent=2))
    return evidence


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context", default="docker-desktop")
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--release", default="delivery")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--hours", type=int, default=168)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            prune(
                args.context,
                args.namespace,
                args.release,
                args.output.resolve(),
                hours=args.hours,
                apply=args.apply,
            ),
            indent=2,
        )
    )
