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
