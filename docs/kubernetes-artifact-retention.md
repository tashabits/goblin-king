# Kubernetes Artifact Retention

Kubernetes task artifacts become durable before the transient worker Job is deleted. The worker
still writes only to its isolated `/artifacts` `emptyDir`; it never receives the control-plane PVC.
The trusted result-forwarder sidecar validates and copies declared files into the configured
artifact PVC, rewrites their result URIs to the API-visible artifact root, and only then publishes
the final result through Redis.

## Default Helm Configuration

The chart enables persistence and uses one PVC for SQLite and artifact storage:

```yaml
persistence:
  enabled: true
  artifactSubdirectory: artifacts

config:
  artifactRoot: /data/artifacts
```

The API and scheduler mount the PVC at `/data`. Dynamically created result forwarders receive only
the `persistence.artifactSubdirectory` projection at `/goblin-retained-artifacts`. The untrusted
worker container cannot see that mount.

`persistence.artifactSubdirectory` must identify the same PVC directory that
`config.artifactRoot` identifies below the API's `/data` mount. With the defaults, both refer to the
PVC's `artifacts` directory. The API creates its configured root during startup. Operators using a
pre-existing claim or a custom scheduler must create the subdirectory before scheduling an
artifact-producing Job.

For a scheduler outside the chart, configure:

```text
GOBLIN_KING_K8S_ARTIFACT_PVC_CLAIM=<claim-name>
GOBLIN_KING_K8S_ARTIFACT_VOLUME_SUBDIRECTORY=artifacts
GOBLIN_KING_K8S_ARTIFACT_URI_ROOT=/data/artifacts
```

If the PVC claim variable is absent, artifact-free results behave normally. A result declaring one
or more artifacts becomes an explicit failed result with no retained artifact metadata. This avoids
claiming that bytes survived when no durable backend was available.

## Retention Contract

For every declared artifact, the forwarder:

1. Requires a unique, single-segment artifact name.
2. Accepts only a relative local path or a local `file://` URI beneath `/artifacts`.
3. Resolves the path and rejects traversal, missing files, directories, remote file authorities,
   non-file URI schemes, and symbolic links.
4. Validates the declared media-type syntax or derives a conservative type from the file name.
5. Enforces the effective resource policy's `artifact_max_files` and `artifact_max_bytes` against
   actual files and copied bytes. Without an effective limit, bounded forwarder defaults are 100
   files and 100 MiB.
6. Computes SHA-256 and byte count. If the worker supplied
   `artifact.<name>.sha256` or `artifact.<name>.bytes` metrics, the actual bytes must match.
7. Copies into a staging directory on the destination filesystem, verifies that the source did not
   change during copying, and atomically renames the complete set into place.
8. Stores bytes below a hashed project scope and hashed Run scope with opaque file names. The public
   artifact name remains in metadata and download headers.
9. Rewrites each URI to the API-visible artifact root and adds actual size, digest, retained-file,
   and retained-byte metrics.

Retention is all-or-nothing for one result. A path, limit, media-type, digest, storage, or concurrent
mutation failure removes all artifact entries and artifact-prefixed metrics from the published
result. A previously failed worker result remains failed after successful retention, so diagnostic
artifacts can survive without hiding the original error.

The immutable Run destination makes a safe forwarder retry idempotent when the bytes match. A retry
that finds different bytes fails rather than replacing prior evidence.

## Downloads And Cleanup

After the Run is persisted, authorized callers use the existing routes:

```text
GET /runs/<run-id>/artifacts
GET /runs/<run-id>/artifacts/<artifact-name>
```

Authorization remains derived from the Run and Job project. The URI is never accepted unless its
resolved path remains below the API's configured artifact root. The download uses attachment
disposition; media type is metadata, not a promise that arbitrary content is safe to render inline.

The existing admin retention policy operates on these retained files:

```bash
curl -X POST http://127.0.0.1:8000/admin/artifacts/cleanup \
  -H "Authorization: Bearer local-dev-token" \
  -H "Content-Type: application/json" \
  -d '{"dry_run":true,"max_total_bytes":0}'
```

Preview first, then repeat with `"dry_run":false`. Cleanup removes bytes, not immutable historical
Run metadata; a later download returns `404`.

## Storage And Availability Limits

The default chart is sufficient for local kind and other single-node clusters. Its `ReadWriteOnce`
PVC may not attach simultaneously to API, scheduler, and worker Pods on different nodes. Multi-node
deployments should select a storage class and access mode that support the required attachment, such
as `ReadWriteMany`, or wait for an object-storage adapter. An object-store backend is not implemented
by this change.

Artifact retention is a durability mechanism, not a malware scanner or trust decision. Only trusted
forwarder images should receive the PVC. Worker images remain subject to the normal image trust,
contract validation, resource policy, network policy, and container-hardening requirements.

## Reproducible Proof

The complete PNG/ZIP, digest, Job-deletion, API-download, and cleanup proof is documented in
[`proofs/issue-147-kubernetes-artifact-retention.md`](proofs/issue-147-kubernetes-artifact-retention.md).
