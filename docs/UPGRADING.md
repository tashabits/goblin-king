# Upgrade Guide

Goblin King upgrades should prove that host-project plugins still discover, build, and
run.

## Before Upgrade

Record:

- Current Goblin King package version.
- API/scheduler/admin image tags.
- Worker image tags.
- Compatibility matrix version.
- Host-project registry and image map paths.

## Upgrade Steps

1. Build the new internal wheel.
2. Install it in the host project or API/scheduler image.
3. Build API, scheduler, and admin images.
4. Build project worker images.
5. Run local CI.
6. Render Helm values or start the Docker host-project fixture.
7. Reload discovery.
8. Submit one short goblin and probe one long-running goblin.
9. Inspect runs, events, heartbeats, artifacts, and audit logs.

## Kubernetes Image Settings

Existing tag-only chart values and Python constructor calls remain valid. New installs
should set `image.digest` when an immutable control-plane identity is available. With no
`scheduler.resultForwarder.image` override, the chart passes that exact rendered image
to generated result-forwarder sidecars.

If an older deployment depended on an out-of-band `goblin-king:local` retag inside its
cluster, remove that workaround after setting the control or separate forwarder image.
Private registries should keep credential data in Kubernetes Secrets and pass only
Secret names through `image.pullSecrets` or `scheduler.workloadImagePullSecrets`.

Before applying, render and confirm digest precedence, forwarder CLI arguments, pull
policies, and Secret names. See [Kubernetes Runtime Images](kubernetes-runtime-images.md).

### Restricted workload migration

Upgrades remain on `scheduler.workloadSecurity.profile: legacy`. This preserves the
established generated Pod shape and avoids changing workers that currently run as root,
write outside contract mounts, or depend on implicit cluster credentials.

Migrate deliberately to `restricted-v1`: verify both images under UID/GID 65532 (or
configured IDs), set `resourcePolicies.defaults.filesystem.read_only_root: true`, render
the chart, revalidate each worker under the new security identity, and run a real Pod
inspection. A configured false read-only-root policy is rejected as a conflict. Review
[Kubernetes Workload Security](kubernetes-workload-security.md) before enabling per-kind
ServiceAccounts.

The versioned restricted forwarder memory default increases from a 16 MiB request/64 MiB limit to a
64 MiB request/128 MiB limit. The packaged retention forwarder exceeded the old limit during live
proof and completed at the new floor. Legacy Pods and worker-resource defaults are unchanged.
Restricted validation identities include these resources, so revalidate each affected kind after
upgrading.

Notebook-authored ASGI Deployments are now always generated with the fixed restricted
service boundary documented in
[Kubernetes Workload Security](kubernetes-workload-security.md). The API, database,
record, runtime, and Service response shapes are unchanged. Declared Python requirements
still install at startup, now into the writable `/tmp/goblin-service-runtime` mount.
Review service source that writes elsewhere on the image filesystem and move that
scratch state beneath `/tmp` before upgrading; automatic ServiceAccount credentials are
no longer available to these service pods.

## Generic Kubernetes Validation Upgrade

This release adds an attainable first-proof path for generic registry workers. No
database migration, API settings migration, registry rewrite, worker image-map rewrite,
or worker contract change is required.

- Existing `goblin-king workers validate` calls still use Docker by default.
- Use `--runtime kubernetes` only when intentionally creating Kubernetes proof.
- `--build` and `--run-root` remain Docker-only.
- `WorkerValidationResult` keeps all existing fields and adds default-empty `artifacts`
  and `logs` fields.
- Existing `KubernetesRuntime` construction and `run(...) -> GoblinResult` behavior are
  unchanged; the additive observed-run path is used by validation to capture diagnostics
  before transient Job cleanup.
- Generic API validation, notebook validation, scheduler execution, and direct
  Kubernetes CLI execution use one typed runtime factory. Existing API settings and CLI
  defaults remain valid; non-default forwarder/pull settings now apply to generic proof
  as well as normal execution.
- When `restricted-v1` is enabled, generic validation identity includes the effective
  workload-security contract and per-kind ServiceAccount. Existing legacy proof becomes
  stale by design and each affected kind must be revalidated.
- Existing SQLite and Redis layouts are unchanged.

After installing the updated API and scheduler images, invoke
`POST /admin/workers/validate-kubernetes` for each generic worker identity before its
first normal Kubernetes job. Prefer digest-pinned image references. See the
[exact proof guide](kubernetes-generic-worker-validation-proof.md).

## Docker Data-Volume Placement

Deployments that set `GOBLIN_KING_DOCKER_DATA_VOLUME` must also set an absolute
`GOBLIN_KING_RUN_ROOT` inside the scheduler's mount of that volume. For example, a
volume mounted at `/data` should use `/data/runs`. This replaces the unsafe implicit
working-directory placement used by earlier builds. Update Compose environment before
starting the upgraded scheduler; see
[Writable Docker Runtime Data](writable-docker-runtime-data.md).

## Event And Run Ordering

The event API now adds a positive `sequence` field. Typed clients should add the field
and order or page by the sequence contract rather than `created_at`. On first open, an
existing database backfills sequences from original insertion order and repairs
historical terminal Runs whose finish precedes their start. See
[Causal Lifecycle Ordering](causal-lifecycle-ordering.md).

No Job status, Run status, constructor requirement, or result-envelope shape changes. Existing
EventRecord construction and the historical `SQLiteStore.save_event()` return contract remain
valid. Redis Stream consumers should deduplicate by `sequence`; pub/sub remains best-effort.

## Live Run Events

This release adds an optional bounded Redis Stream for worker-authored `progress`,
`stdout`, `stderr`, and `message` records. Docker and Kubernetes inject the same
`GOBLIN_RUN_EVENT_*` environment contract. Existing workers can ignore these variables,
and there is no SQLite migration or change to existing Job, Run, result, heartbeat,
status, or durable event response shapes.

New clients can replay `GET /runs/{run_id}/events` and follow
`WS /ws/runs/{run_id}/events`, resuming with the last accepted sequence. Both routes
apply the existing project boundary. The channel depends on the configured Redis
transport, retains a finite window, and is operational feedback rather than durable
result storage. Python worker code may use the newly exported `RunEventPublisher`,
`RunEventEnvelope`, `RunEventRecord`, and `WORKER_RUN_EVENT_CONTRACT_VERSION` root APIs.
See [Live Run Events](live-run-events.md).

The scheduler now persists the running Run identity immediately before worker execution
and finalizes that exact row once. Existing `SQLiteStore.save_run()` callers retain
insert-only behavior, and no database migration is required.

## Docker Artifact Downloads

No worker or result-envelope change is required. Docker artifacts already using the
documented `artifact://<name>` locator are now downloadable through
`GET /runs/{run_id}/artifacts/{artifact_name}`. The locator must match the declared
artifact name and resolve inside the exact job artifact directory; malformed,
mismatched, or escaping paths remain unavailable. Relative paths, supported local
`file://` locators, Kubernetes-retained artifacts, authorization, and cleanup behavior
are unchanged.

## Kubernetes Artifact Retention

The Helm chart now passes its data-PVC claim and artifact mapping to Kubernetes result
forwarders. There is no SQLite migration and Docker behavior is unchanged. Existing
artifact metadata whose bytes were already lost is not reconstructed.

The default mapping is `persistence.artifactSubdirectory: artifacts` and
`config.artifactRoot: /data/artifacts`. Deployments with a custom artifact root must set
the PVC subdirectory to the corresponding path below the API's `/data` mount and ensure
that directory exists before artifact Jobs start. Custom non-chart schedulers configure
`GOBLIN_KING_K8S_ARTIFACT_PVC_CLAIM`,
`GOBLIN_KING_K8S_ARTIFACT_VOLUME_SUBDIRECTORY`, and
`GOBLIN_KING_K8S_ARTIFACT_URI_ROOT`.

`KubernetesRuntime` has one optional, defaulted artifact-retention constructor argument;
existing callers do not change. `GoblinResult` and `ArtifactRecord` shapes are unchanged.
Successfully retained results add actual `artifact.<name>.bytes`,
`artifact.<name>.sha256`, `artifact.retained.files`, and `artifact.retained.bytes`
metrics. When a Kubernetes result declares artifacts but retention is not configured or
cannot complete, the result is failed explicitly and artifact entries are omitted.

Custom forwarder images retain the earlier inline Python-plus-Redis command contract while
artifact retention is disabled. Enabling PVC retention switches the sidecar to the packaged
forwarder module, so a separate forwarder image must contain the same Goblin King version as the
control plane before the PVC settings are enabled.

Revalidate workers after enabling retention or changing the claim, volume subdirectory, URI root,
or forwarder mount path. Those normalized values are part of the Kubernetes scheduler identity for
both legacy and restricted profiles. The no-retention legacy identity remains unchanged.

The artifact root and retained directories must be group-accessible to the forwarder's `fsGroup`.
The API prepares them as setgid `02770` with retained files `0660`. If the API runs non-root, arrange
the same group and directory mode through the storage class, kubelet `fsGroup`, or an initializer
before upgrade; an already-correct root is accepted without privileged ownership changes.

The chart's default `ReadWriteOnce` PVC is appropriate for the documented single-node
local cluster. Review access modes before a multi-node upgrade; use `ReadWriteMany` or an
equivalent backend when API and worker Pods may run on different nodes.

## Compatibility Fixtures

The `examples/compatibility/project-ready-v0_1/` fixture is the baseline for the
`0.1.0` project-ready contract. Future incompatible changes should add a new fixture and
leave older fixtures in place.

## Failure Policy

If discovery reload fails, the previous valid registry should remain active. Fix the
registry, image map, or package entry point, then reload again before submitting new
work.
