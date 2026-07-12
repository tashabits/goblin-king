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

## Compatibility Fixtures

The `examples/compatibility/project-ready-v0_1/` fixture is the baseline for the
`0.1.0` project-ready contract. Future incompatible changes should add a new fixture and
leave older fixtures in place.

## Failure Policy

If discovery reload fails, the previous valid registry should remain active. Fix the
registry, image map, or package entry point, then reload again before submitting new
work.
