# Pipeline foundation storage

I use `sqlite-single-node` for the initial delivery foundation. The API, scheduler,
and optional repository API run as one replica each, pinned to the same
`kubernetes.io/hostname`, and mount one ReadWriteOnce PVC. SQLite serializes their
write transactions. This is one storage node with multiple client processes,
not a highly available database or a distributed release lock.

```yaml
persistence:
  topology: sqlite-single-node
  sqliteNode: worker-a
  enabled: true
  storageClassName: local-block
  accessModes: [ReadWriteOnce]
config:
  dbPath: /data/goblin-king.sqlite3
```

Replace the hostname and StorageClass with the installation's actual values.
Select node-local storage or a block device mounted with a local filesystem that
provides reliable SQLite locking and synchronization. The chart cannot inspect a
StorageClass's underlying filesystem. NFS/SMB/RWX file sharing is not supported by
this mode. [SQLite explains the filesystem requirements](https://www.sqlite.org/useovernet.html).
[ReadWriteOnce permits multiple pods on one node](https://kubernetes.io/docs/concepts/storage/persistent-volumes/#access-modes);
it does not restrict writes to one process.

The chart rejects missing persistence, a missing hostname, other volume access
modes, multiple enabled storage-client replicas, autoscaling, conflicting hostname
selectors, database paths outside `/data/<name>.sqlite3`, project settings that
could override storage, and extra mounts covering data or configuration. All
three storage clients use `Recreate` updates to avoid surge replicas. Additional
node selectors, affinity, and tolerations still apply and can make a pod Pending;
they cannot move it away from the selected hostname. API startup, readiness, and
liveness probes check `/health`; these indicate HTTP availability, not completion
of a delivery operation.

`persistence.topology: legacy` remains the default for compatibility with existing
chart values. It provides no new placement or replica guarantee and is not the
supported pipeline storage configuration. Delivery management is enabled separately
through `pipeline.enabled` and its verified repository registrations.

## Recovery and upgrades

I keep the hostname fixed during ordinary upgrades. Before enabling this mode on
an existing release, stop its storage clients, take a consistent backup, verify
the PVC's actual node and filesystem, and render the new values. Incompatible
placement should leave clients Pending rather than start them with new storage.

If the node is unavailable, clients remain unavailable. There is no automatic
failover to another node. For node-local storage, replacing the node can lose the
volume; a PVC object alone is not a backup. Recovery requires fencing the old
node/processes, restoring a verified backup onto approved storage, checking the
restored state, and explicitly selecting the replacement hostname. Do not change
the selector while an old writer may still be running. Node replacement and
recovery on an adopting cluster remain separate acceptance checks.

Use SQLite's online backup API or a coordinated stopped-writer snapshot. Do not
copy only the live database file while journal/WAL writes may be in flight. Keep
backups outside the source PVC and test restoration. A database backup does not
include artifacts, registry images, application volumes, or externally managed
credentials; those need their own retention and recovery procedures.

The delivery chart marks its SQLite PVC for retention on Helm uninstall. I still
export and verify a backup before uninstalling an installation with valuable state;
retention does not protect against namespace deletion or disk loss. Older releases
need an upgrade before their recorded Helm manifests include that retention policy.
The proof below intentionally creates and deletes disposable fixture data.

## Delivery backup commands

I use `scripts/pipeline_storage_recovery.py` for a fenced delivery installation.
`backup` stops storage clients and exports a consistent state archive to the host.
`artifacts` exports the recorded OCI image graphs to a separate archive and binds
its checksum to that database snapshot. `restore --node REPLACEMENT_NODE` creates
a separate PVC and starts the API in recovery mode with scheduling disabled.
`reconcile --enable` verifies actual releases and service health before enabling
delivery. All commands require `--namespace` and `--output`; `--context` defaults
to `docker-desktop` and `--release` to `delivery`.

For registry loss, I first provision an empty trusted registry, then run
`restore-artifacts --registry-config PATH` while the original state remains fenced.
The local configuration specifies a host-reachable HTTPS `endpoint` and a
`repositories` mapping from each exact repository path to its publication Secret
reference, with `namespace` and `name` fields. Each Secret supplies `username`,
`password`, and `ca.crt`. Credentials stay in Kubernetes. Restoration checks the
entire backup before publishing, preserves immutable build tags and image digests,
then reads back every restored blob to verify its size and checksum. A conflicting
existing tag fails restoration. The command writes `artifact-restoration.json`
beside the backup. Registry endpoint/trust changes and application data restoration
remain separate operator steps before release reconciliation.

## Execution retention

I retain execution results and identities in SQLite and the execution ConfigMaps.
`scripts/pipeline_retention.py` previews expired Jobs and scratch claims after both
the database and execution record acknowledge the same result. The default age is
seven days; the minimum is one hour. Held, active, unacknowledged, and conflicting
executions are excluded. Terminal container state and original resource UIDs are
checked before deletion.

```powershell
python scripts/pipeline_retention.py --namespace DELIVERY_NAMESPACE `
  --output C:\Backups\goblin-king\execution-retention --hours 168
```

The preview exports evidence and checksums outside the cluster. Add `--apply` to
repeat the checks and remove eligible resources with UID and version preconditions.
Execution ConfigMaps, database history, published images, and application data stay
available. Older builds without recorded scratch-volume identities keep their claims;
I do not infer ownership from a reused name. Export these records with the database
backup before any broader archival policy removes them.

## Executable cluster proof

Build an image with a fresh tag and make it available to the target cluster. The
Docker Desktop kind setup used for the recorded proof fetched the locally built
image through its registry mirror. Other clusters need an explicit image import
or registry publication. The harness does not change registry configuration.

```powershell
docker build -t goblin-king:pipeline-foundation-proof .
python scripts/pipeline_cluster_proof.py `
  --context docker-desktop `
  --node desktop-worker `
  --image goblin-king:pipeline-foundation-proof `
  --storage-class hostpath `
  --output .runlogs/pipeline-cluster-proof `
  --cleanup
```

Each run requires a new output directory and creates a randomly named namespace.
It never adopts an existing namespace or release. The fixture token is public
test data and the admin ingress is disabled. All commands specify the context
and namespace. On failure, the namespace is retained for diagnosis; its name and
UID are written to `summary.json`. `--cleanup` removes only the successful run's
owned namespace, after checking its UID. Without that option, the fixture remains
available for inspection. The local evidence and backup remain after cleanup.

The harness checks a fresh Helm installation, actual colocated pods, admission
from one process and replay from another, real service HTTP authentication and
persisted audit reads, a second Helm revision, replacement of every storage
client, and replay through the replacement repository API pod. It exports an
online backup to the host, copies it into a pod's separate ephemeral filesystem,
and verifies the original build ID plus `PRAGMA integrity_check` there. Verification
fails if the expected record is absent before replay, so an empty database cannot
produce a false restart success.

Evidence includes commands, chart values, source revision and file hashes, pod
UIDs and image IDs, node metadata, Helm history, PVC metadata, and the SQLite
backup. The backup can contain installation data: use the harness only with its
isolated fixture namespace and handle retained evidence appropriately.

This proves foundation persistence on one selected node of the observed cluster.
It does not prove worker execution, browser admin workflows, application rollout,
node-loss recovery, application backup, or the complete delivery acceptance matrix.
