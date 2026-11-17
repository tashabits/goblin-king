"""Verify local CRI index aliases against the exact node-platform runtime manifest."""

import hashlib
import json
import re
import subprocess


def verify_platform_manifests(read_blob, expected, observed, platform):
    records = {}

    def selected(digest, depth=0):
        if depth > 4 or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
            raise RuntimeError("invalid or excessively nested cached image manifest")
        raw = read_blob(digest)
        if len(raw) > 8 * 1024**2 or "sha256:" + hashlib.sha256(raw).hexdigest() != digest:
            raise RuntimeError("cached manifest checksum differs from its digest")
        document = json.loads(raw)
        records[digest] = document
        if document.get("schemaVersion") != 2:
            raise RuntimeError("unsupported cached image manifest schema")
        if "manifests" not in document:
            if "config" not in document or not isinstance(document.get("layers"), list):
                raise RuntimeError("cached image does not contain a runtime manifest")
            return digest
        candidates = [
            entry
            for entry in document["manifests"]
            if entry.get("platform", {}).get("os") == platform["operatingSystem"]
            and entry.get("platform", {}).get("architecture") == platform["architecture"]
        ]
        if len(candidates) != 1:
            raise RuntimeError("image index does not select exactly one node platform")
        return selected(candidates[0]["digest"], depth + 1)

    manifest = selected(expected)
    if selected(observed) != manifest:
        raise RuntimeError("observed runtime differs from the approved platform manifest")
    return {"platform_manifest": manifest, "manifests": records}


def verify_platform_alias(output, pod, expected, tag, observed, context="docker-desktop"):
    def command(*args):
        return subprocess.check_output(args, timeout=30)

    node = pod["spec"]["nodeName"]
    container = json.loads(command("docker", "inspect", node))[0]
    if container["Config"]["Labels"].get("io.x-k8s.kind.cluster") != "desktop":
        raise RuntimeError("image verification requires the identified local kind node")
    kube_node = json.loads(
        command("kubectl", "--context", context, "get", "node", node, "-o", "json")
    )
    addresses = {
        entry["address"]
        for entry in kube_node["status"]["addresses"]
        if entry["type"] == "InternalIP"
    }
    if not addresses.intersection(
        value["IPAddress"] for value in container["NetworkSettings"]["Networks"].values()
    ):
        raise RuntimeError("Docker and Kubernetes node identities differ")
    reference = tag
    if "/" not in tag:
        reference = "docker.io/library/" + tag
    elif not any(character in tag.split("/", 1)[0] for character in (".", ":")):
        if not tag.startswith("localhost/"):
            reference = "docker.io/" + tag
    container_id = container["Id"]
    listing = command(
        "docker", "exec", container_id, "ctr", "-n", "k8s.io", "images", "ls", "name==" + reference
    ).decode()
    rows = listing.splitlines()[1:]
    if len(rows) != 1 or rows[0].split()[2] != expected:
        raise RuntimeError("cached reference does not resolve to the approved immutable index")
    actual = observed.rsplit("@", 1)[-1].removeprefix("containerd://")
    identity = verify_platform_manifests(
        lambda digest: command(
            "docker", "exec", container_id, "ctr", "-n", "k8s.io", "content", "get", digest
        ),
        expected,
        actual,
        kube_node["status"]["nodeInfo"],
    )
    current = json.loads(
        command("kubectl", "--context", context, "get", "node", node, "-o", "json")
    )
    if current["metadata"]["uid"] != kube_node["metadata"]["uid"]:
        raise RuntimeError("node identity changed during image verification")
    evidence = {
        "node": node,
        "node_uid": kube_node["metadata"]["uid"],
        "container_id": container_id,
        "pod_uid": pod["metadata"]["uid"],
        "tag": tag,
        "expected_index": expected,
        "observed_index": actual,
        "tag_reference": listing,
        **identity,
    }
    with (output / "verified-platform-aliases.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(evidence) + "\n")
    return evidence
