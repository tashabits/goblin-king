"""Upgrade a separate installation of the last proved delivery revision with networking off."""

from __future__ import annotations

import io
import json
import subprocess
import tarfile
import time

from pipeline_acceptance import ROOT, Acceptance
from pipeline_delivery_acceptance import DeliveryAcceptance

BASELINE = "437d2ca3debcdc6438dd15568fc62f4d2cc69ba6"


def upgrade(network):
    proof = network.proof
    output = network.output / "upgrade"
    output.mkdir(parents=True, exist_ok=True)
    child = Acceptance(output)
    images = {}
    for role in ("controller", "builder", "provider", "deployer", "admin"):
        repository = "goblin-king-admin-ui" if role == "admin" else "goblin-king"
        tag = repository + ":pipeline-" + role + "-" + BASELINE[:12]
        inspected = json.loads(proof.command(["docker", "image", "inspect", tag]))[0]
        labels = inspected["Config"].get("Labels", {})
        if (
            labels.get("org.opencontainers.image.revision") != BASELINE
            or labels.get("io.goblin-king.source-dirty") != "false"
        ):
            raise RuntimeError("network upgrade requires the clean proved baseline images")
        images[role] = {
            "tag": tag,
            "digest": inspected["RepoDigests"][0],
            "source_revision": BASELINE,
            "source_dirty": False,
        }
    child.state.update(
        context=proof.state["context"],
        run_id=proof.state["run_id"],
        images=images,
        namespaces=proof.state["namespaces"],
    )
    delivery = DeliveryAcceptance(child)
    delivery.prefix = network.delivery.prefix + "-upgrade"
    delivery.control, delivery.provider = delivery.prefix + "-demo", delivery.prefix + "-provider"
    raw = subprocess.check_output(["git", "archive", BASELINE, "charts/goblin-king"], timeout=30)
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        archive.extractall(output, filter="data")
    delivery.checkpoint(
        "baseline-installation", lambda: delivery.installation(output / "charts/goblin-king")
    )
    proof.state["namespaces"].update(child.state["namespaces"])
    proof.save()
    delivery.checkpoint("baseline-onboarding", lambda: delivery.onboard("python", 1))
    merged = delivery.merge("network-upgrade-baseline", "python", "test")
    built = delivery.build(merged)
    if built["status"] != "published":
        raise RuntimeError("baseline upgrade fixture did not build successfully")
    delivery.deployed("python", "test", built["id"])

    def inventory():
        release = delivery.release("python", "test")
        return {
            "repositories": delivery.collection("repositories"),
            "builds": delivery.collection("builds"),
            "releases": delivery.collection("releases"),
            "health": delivery.route_health(release),
            "jobs": json.loads(proof.kube("-n", delivery.control, "get", "jobs", "-o", "json"))[
                "items"
            ],
        }

    from pipeline_fault_acceptance import FaultAcceptance

    FaultAcceptance(delivery).wait_for_acknowledgements()
    before = delivery.checkpoint("baseline-before-upgrade", inventory)
    values = json.loads((delivery.output / "values.json").read_text())
    digest = proof.state["images"]["controller"]["digest"]
    values["image"] = {"repository": digest.split("@")[0], "digest": digest.split("@")[1]}
    admin = proof.state["images"]["admin"]["digest"]
    values["admin"]["image"] = {"repository": admin.split("@")[0], "digest": admin.split("@")[1]}
    values["pipeline"]["settings"].update(networking={}, network_image="")
    path = output / "upgrade-values.json"
    path.write_text(json.dumps(values, indent=2))
    proof.command(
        [
            "helm",
            "--kube-context",
            proof.state["context"],
            "upgrade",
            "delivery",
            str(ROOT / "charts/goblin-king"),
            "-n",
            delivery.control,
            "-f",
            str(path),
            "--wait",
            "--timeout=300s",
        ],
        timeout=330,
    )
    time.sleep(12)
    after = inventory()
    for key in ("repositories", "builds", "releases", "health"):
        if before[key] != after[key]:
            raise RuntimeError("networking-disabled upgrade changed existing " + key)
    if {item["metadata"]["uid"] for item in before["jobs"]} != {
        item["metadata"]["uid"] for item in after["jobs"]
    }:
        raise RuntimeError("networking-disabled upgrade dispatched new work")
    network_state = delivery.request(
        "/pipeline/releases/" + delivery.release("python", "test")["key"] + "/networking"
    )
    if network_state["network_version"] or network_state["observed"]:
        raise RuntimeError("upgrade enabled networking on an existing release")
    resources = json.loads(
        proof.kube(
            "get",
            "httproutes,gateways,deployments",
            "-A",
            "-l",
            "goblin-king.io/networking=true",
            "-o",
            "json",
        )
    )["items"]
    if any(item["metadata"]["namespace"].startswith(delivery.prefix + "-") for item in resources):
        raise RuntimeError("disabled upgrade created network resources")
    result = network.save(
        "network-upgrade",
        {
            "baseline": BASELINE,
            "images": images,
            "before": before,
            "after": after,
            "networking": network_state,
        },
    )
    # This case intentionally retains a hold. Keep its real execution isolated from
    # the four releases used by the remaining networking and cleanup checks.
    FaultAcceptance(delivery).run_point("kubernetes.before_create")
    retired = []
    for entry in child.state["namespaces"].values():
        namespace = entry["name"]
        if not namespace.startswith(delivery.prefix + "-"):
            continue
        actual = json.loads(proof.kube("get", "namespace", namespace, "-o", "json"))
        if (
            actual["metadata"]["uid"] != entry["uid"]
            or actual["metadata"]["labels"].get("goblin-king.io/acceptance")
            != proof.state["run_id"]
        ):
            raise RuntimeError("completed upgrade fixture ownership changed")
        for kind, field, value in (("deployment", "replicas", 0), ("cronjob", "suspend", True)):
            for item in json.loads(proof.kube("-n", namespace, "get", kind, "-o", "json"))["items"]:
                patch = [
                    {"op": "test", "path": "/metadata/uid", "value": item["metadata"]["uid"]},
                    {"op": "replace", "path": "/spec/" + field, "value": value},
                ]
                proof.kube(
                    "-n",
                    namespace,
                    "patch",
                    kind,
                    item["metadata"]["name"],
                    "--type=json",
                    "-p",
                    json.dumps(patch),
                )
                retired.append(
                    {"namespace": namespace, "kind": kind, "uid": item["metadata"]["uid"]}
                )
        for item in json.loads(proof.kube("-n", namespace, "get", "daemonsets", "-o", "json"))[
            "items"
        ]:
            if item["metadata"]["name"] != "pipeline-image-cache":
                raise RuntimeError("unexpected DaemonSet in the completed upgrade fixture")
            proof.kube(
                "delete",
                "--raw",
                f"/apis/apps/v1/namespaces/{namespace}/daemonsets/pipeline-image-cache",
                "-f",
                "-",
                document={
                    "apiVersion": "v1",
                    "kind": "DeleteOptions",
                    "preconditions": {"uid": item["metadata"]["uid"]},
                },
            )
    (output / "retired-workloads.json").write_text(
        json.dumps({"retired": retired, "data_retained": True}, indent=2)
    )
    return result
