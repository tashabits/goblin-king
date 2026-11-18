"""Exercise registry-side authorization and immutable tags over verified TLS."""

import argparse
import base64
import hashlib
import json
import subprocess
from pathlib import Path
from uuid import uuid4

from goblin_king.pipeline_oci import MANIFEST_TYPE, RegistryClient


def prove(context, namespace, endpoint, output):
    def secret(name):
        result = subprocess.run(
            ["kubectl", "--context", context, "-n", namespace, "get", "secret", name, "-o", "json"],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        return {
            key: base64.b64decode(value).decode()
            for key, value in json.loads(result.stdout)["data"].items()
        }

    output.mkdir(parents=True, exist_ok=True)
    account = secret("python-publish")
    ca = output / "ca.crt"
    ca.write_text(account["ca.crt"])
    client = RegistryClient(endpoint, account["username"], account["password"], ca)
    config = json.dumps(
        {
            "architecture": "amd64",
            "os": "linux",
            "config": {},
            "rootfs": {"type": "layers", "diff_ids": []},
        }
    ).encode()
    digest = "sha256:" + hashlib.sha256(config).hexdigest()
    repository = "python/registry-proof"
    tag = str(uuid4())
    client.blob(repository, digest, config)
    manifest = {
        "schemaVersion": 2,
        "mediaType": MANIFEST_TYPE,
        "layers": [],
        "config": {
            "mediaType": "application/vnd.oci.image.config.v1+json",
            "digest": digest,
            "size": len(config),
        },
    }
    raw = json.dumps(manifest).encode()
    published = client.manifest(repository, tag, raw)
    other, _, _ = client.request(
        "/v2/node/forbidden/blobs/uploads/", method="POST", data=b"", expected=(401, 403)
    )
    manifest["annotations"] = {"proof": "overwrite-must-be-denied"}
    overwrite, _, _ = client.request(
        f"/v2/{repository}/manifests/{tag}",
        method="PUT",
        data=json.dumps(manifest).encode(),
        content_type=MANIFEST_TYPE,
        expected=(401, 403, 405),
    )
    _, _, actual = client.request(f"/v2/{repository}/manifests/{tag}")
    if actual != raw:
        raise RuntimeError("immutable manifest changed")
    proof = {
        "status": "passed",
        "registry": endpoint,
        "digest": published,
        "tag": tag,
        "tls_verification": True,
        "cross_repository_status": other,
        "overwrite_status": overwrite,
        "immutable_manifest_preserved": True,
    }
    (output / "registry-proof.json").write_text(json.dumps(proof, indent=2) + "\n")
    print(json.dumps(proof))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context", default="docker-desktop")
    parser.add_argument("--namespace", default="gk-local-registry")
    parser.add_argument("--endpoint", default="https://127.0.0.1:8443")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    prove(args.context, args.namespace, args.endpoint, args.output)
