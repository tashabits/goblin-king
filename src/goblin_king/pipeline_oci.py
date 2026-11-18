"""Validate staged OCI artifacts and publish through a narrowly scoped registry identity."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import ssl
import tarfile
from pathlib import Path, PurePosixPath
from urllib.error import HTTPError
from urllib.parse import urlencode, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

from goblin_king.pipeline_faults import no_fault

DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
MANIFEST_TYPE = "application/vnd.oci.image.manifest.v1+json"


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class RegistryError(RuntimeError):
    pass


class RegistryClient:
    """Keep credentials on one TLS origin and reject unexpected upload redirects."""

    def __init__(
        self, endpoint, username, password, ca_path, *, fault_hook=no_fault, fault_identity=""
    ):
        if urlsplit(endpoint).scheme != "https":
            raise ValueError("registry requires HTTPS")
        self.endpoint = endpoint.rstrip("/")
        self.authorization = "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()
        context = ssl.create_default_context(cafile=str(ca_path))
        self.opener = build_opener(NoRedirect(), HTTPSHandler(context=context))
        self.fault_hook, self.fault_identity = fault_hook, fault_identity

    def request(self, path, *, method="GET", data=None, content_type=None, expected=(200,)):
        url = urljoin(self.endpoint + "/", path)
        if urlsplit(url)[:2] != urlsplit(self.endpoint)[:2]:
            raise RegistryError("registry attempted a cross-origin credentialed request")
        headers = {"Authorization": self.authorization, "Accept": MANIFEST_TYPE}
        if content_type:
            headers["Content-Type"] = content_type
        request = Request(url, method=method, data=data, headers=headers)
        point = (
            "manifest"
            if method == "PUT" and "/manifests/" in urlsplit(url).path
            else "blob"
            if method == "PUT"
            else "upload"
            if method == "POST"
            else None
        )
        if point:
            self.fault_hook("registry.before_" + point, self.fault_identity)
        try:
            with self.opener.open(request, timeout=60) as response:
                if response.status not in expected:
                    raise RegistryError(f"unexpected registry status {response.status}")
                result = response.status, dict(response.headers), response.read(8 * 1024 * 1024)
                if point:
                    self.fault_hook("registry.after_" + point, self.fault_identity)
                return result
        except HTTPError as error:
            if error.code in expected:
                return error.code, dict(error.headers), error.read(8192)
            raise RegistryError(f"registry rejected {method} ({error.code})") from None

    def blob(self, repository, digest, data):
        if not DIGEST.fullmatch(digest) or hashlib.sha256(data).hexdigest() != digest[7:]:
            raise ValueError("blob checksum mismatch")
        status, _, _ = self.request(
            f"/v2/{repository}/blobs/{digest}", method="HEAD", expected=(200, 404)
        )
        if status == 200:
            return
        _, headers, _ = self.request(
            f"/v2/{repository}/blobs/uploads/", method="POST", data=b"", expected=(202,)
        )
        location = headers.get("Location") or headers.get("location")
        if not location:
            raise RegistryError("registry omitted upload location")
        location += ("&" if "?" in location else "?") + urlencode({"digest": digest})
        self.request(
            location,
            method="PUT",
            data=data,
            content_type="application/octet-stream",
            expected=(201,),
        )

    def manifest(self, repository, tag, raw):
        digest = "sha256:" + hashlib.sha256(raw).hexdigest()
        status, _, prior = self.request(f"/v2/{repository}/manifests/{tag}", expected=(200, 404))
        if status == 200:
            if hashlib.sha256(prior).hexdigest() != digest[7:]:
                raise RegistryError("immutable tag already identifies different content")
            return digest
        self.request(
            f"/v2/{repository}/manifests/{tag}",
            method="PUT",
            data=raw,
            content_type=MANIFEST_TYPE,
            expected=(201,),
        )
        _, headers, observed = self.request(f"/v2/{repository}/manifests/{digest}")
        if observed != raw:
            raise RegistryError("registry manifest bytes do not match the published digest")
        return digest


def validate_oci(archive: Path, *, expected_labels: dict, max_bytes=512 * 1024**2):
    """Inspect a closed sandbox's untrusted archive without extracting filesystem entries."""
    with tarfile.open(archive, "r:*") as source:
        entries = {}
        size = 0
        for member in source:
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts or member.issym() or member.islnk():
                raise ValueError("unsafe OCI archive entry")
            if member.isdir():
                continue
            if not member.isfile() or member.name in entries:
                raise ValueError("duplicate or non-file OCI archive entry")
            size += member.size
            if size > max_bytes or len(entries) >= 4096:
                raise ValueError("OCI artifact exceeds resource bounds")
            entries[member.name] = member

        def read(name, maximum=8 * 1024 * 1024):
            member = entries.get(name)
            if member is None or member.size > maximum:
                raise ValueError("OCI metadata is absent or exceeds bounds")
            with source.extractfile(member) as stream:
                return stream.read()

        def descriptor(item, maximum=8 * 1024 * 1024):
            digest = item.get("digest", "")
            if not DIGEST.fullmatch(digest) or item.get("urls"):
                raise ValueError("OCI descriptor must reference a local SHA-256 blob")
            raw = read("blobs/sha256/" + digest[7:], maximum)
            if len(raw) != item["size"] or hashlib.sha256(raw).hexdigest() != digest[7:]:
                raise ValueError("OCI descriptor checksum or size mismatch")
            return raw

        if json.loads(read("oci-layout")) != {"imageLayoutVersion": "1.0.0"}:
            raise ValueError("unsupported OCI layout")
        index = json.loads(read("index.json"))
        if len(index.get("manifests", [])) != 1:
            raise ValueError("exactly one platform manifest is required")
        item = index["manifests"][0]
        if item["mediaType"] != MANIFEST_TYPE:
            raise ValueError("OCI image manifest required")
        raw_manifest = descriptor(item)
        manifest = json.loads(raw_manifest)
        config_raw = descriptor(manifest["config"])
        config = json.loads(config_raw)
        if config.get("os") != "linux" or config.get("architecture") != "amd64":
            raise ValueError("expected a linux/amd64 runtime image")
        labels = config.get("config", {}).get("Labels", {})
        if any(labels.get(key) != value for key, value in expected_labels.items()):
            raise ValueError("image provenance labels do not match the approved build")
        descriptors = [manifest["config"], *manifest["layers"]]
        for blob in descriptors:
            descriptor(blob, maximum=max_bytes)
        with archive.open("rb") as archive_stream:
            archive_sha256 = hashlib.file_digest(archive_stream, "sha256").hexdigest()
        return {
            "digest": item["digest"],
            "manifest": raw_manifest,
            "blobs": {item["digest"]: descriptor(item, maximum=max_bytes) for item in descriptors},
            "archive_sha256": archive_sha256,
            "labels": labels,
        }


def publish_oci(client, archive, repository, build_id, expected_labels):
    artifact = validate_oci(archive, expected_labels=expected_labels)
    for digest, data in artifact["blobs"].items():
        client.blob(repository, digest, data)
    digest = client.manifest(repository, build_id, artifact["manifest"])
    if digest != artifact["digest"]:
        raise RegistryError("published digest differs from the verified artifact")
    return {
        "digest": digest,
        "archive_sha256": artifact["archive_sha256"],
        "labels": artifact["labels"],
        "repository": repository,
        "build_id": build_id,
    }
