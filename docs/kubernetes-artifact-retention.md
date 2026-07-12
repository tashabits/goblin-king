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

Retained directories use setgid mode `02770` and retained files use `0660`. Under `restricted-v1`,
the generated worker Pod declares the configured `fsGroup`; API startup aligns the artifact root to
that group and mode so the non-root forwarder can create the hashed Run directory. A control plane
that itself runs without root must receive an already prepared root with the same group and mode
(for Helm, set `podSecurityContext.fsGroup` consistently). Startup makes no ownership or mode call
when the root is already correct; it fails visibly rather than silently running with an unreadable
PVC.

For a scheduler outside the chart, configure:

```text
GOBLIN_KING_K8S_ARTIFACT_PVC_CLAIM=<claim-name>
GOBLIN_KING_K8S_ARTIFACT_VOLUME_SUBDIRECTORY=artifacts
GOBLIN_KING_K8S_ARTIFACT_URI_ROOT=/data/artifacts
```

If the PVC claim variable is absent, artifact-free results behave normally. A result declaring one
or more artifacts becomes an explicit failed result with no retained artifact metadata. This avoids
claiming that bytes survived when no durable backend was available.

Without a PVC, generated Pods keep the established inline `python -c` forwarder contract, so a
custom forwarder image that provides Python and Redis remains compatible. Enabling retention uses
the version-matched `goblin_king.kubernetes_result_forwarder` module from the control image because
the retention validator and copier are part of that package. Operators using a separate forwarder
image must publish the same Goblin King version before enabling the PVC settings.

With `restricted-v1`, retention mounts are composed before the security profile is applied. The
forwarder has a read-only root filesystem, reads the transient `/artifacts` mount read-only, and
writes only to `/goblin-result` plus the configured PVC `subPath` mounted at
`/goblin-retained-artifacts`. The worker still has no PVC mount. Linux read-only-root policy does not
make mounted volumes read-only, so the per-mount `readOnly` flag and narrow PVC projection remain
the relevant write boundary.

## Retention Contract

For every declared artifact, the forwarder:

1. Requires a unique, single-segment artifact name.
2. Accepts only a relative local path or a local `file://` URI beneath `/artifacts`.
3. Resolves the path and rejects traversal, missing files, directories, remote file authorities,
   non-file URI schemes, and symbolic links. On POSIX, each path component is opened relative to a
   directory descriptor with no-follow flags, and hashing plus before/after checks use that same
   file descriptor. The portable fallback rechecks components and requires the opened file identity
   to match the identity validated before the copy.
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
artifacts can survive without hiding the original error. If the worker writes and forwards a result
but its container still exits nonzero, the runtime reports a failed Job while preserving only the
artifact metadata already proven durable by the forwarder.

Observed Kubernetes runs load that final forwarded envelope before capturing bounded worker and
result-forwarder logs, the worker exit code, and then deleting the transient Job and ConfigMap.
Generic validation uses this observed path, so a failed worker Job can report both its final logs
and successfully retained diagnostic artifacts. The established `run()` method still returns only
the `GoblinResult`; observation fields are additive for validation and diagnostic callers.

Retention-enabled workers and forwarders do not race on one Redis key. The worker may publish its
legacy envelope for compatibility, while the packaged forwarder publishes the retained envelope on
a separate forwarder-owned key. Retention-enabled runtime code consumes only that second key;
retention-disabled runtime code keeps the original worker-key behavior.

Validation Runs have no durable Run owner for retained bytes. Generic, notebook, and repository
Kubernetes validation therefore remove the validation Run's hashed directory after collecting the
returned metadata and prune its empty parents. Cleanup failure makes validation fail visibly. Normal
scheduled Runs keep their retained bytes for authorized download and policy cleanup.

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

The complete validation-first, PNG/ZIP, digest, Job-deletion, API-download, and cleanup proof is
documented in
[`proofs/issue-147-kubernetes-artifact-retention.md`](proofs/issue-147-kubernetes-artifact-retention.md).
