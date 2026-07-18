# Changelog

## Unreleased

- Map `filesystem.tmpfs` resource policies to worker-only, memory-backed Kubernetes
  `emptyDir` volumes, preserve declared size bounds, and reject options the runtime cannot
  faithfully enforce instead of silently ignoring them.
- Preserve bounded user-worker output for ordinary Kubernetes runs before transient Job
  cleanup and emit it through `worker.container_logs`. Kubernetes marks the stream as
  combined, leaves `stderr` empty, and never presents result-forwarder diagnostics as user
  output; Docker event payloads remain unchanged.
- Emit a standards-derived local `file:` URI from the Node artifact behavior example so the
  unchanged result envelope works with both Docker downloads and Kubernetes durable retention.
- Add optional, registry-owned `metadata.validation_input` for bounded just-in-time contract
  validation while preserving the queued runtime input exactly for real worker execution. Invalid
  validation metadata now fails visibly, and definitions without it retain their existing behavior.
- Add an opt-in, project-authorized live run-event channel with identical Docker and Kubernetes
  worker contracts, bounded Redis Stream replay, and no changes to existing task payload or status
  shapes.
- Serve Docker worker artifacts that use the documented `artifact://<name>` locator through the
  authenticated Run artifact download API. Locator and declared artifact names must match, and
  existing root-containment checks remain in force.

## 0.1.0 - Project-Ready Internal Baseline

- Added typed goblin contracts, registry loading, runtime execution, SQLite persistence,
  scheduler behavior, Docker/Kubernetes execution options, FastAPI control plane, React
  admin, fanout/retry APIs, events/heartbeats, and API hardening.
- Added reusable project settings, plugin templates, entry-point discovery, deploy-time
  discovery reload, and host-project Docker/Helm integration.
- Added internal package boundary docs, adoption docs, release checklist, compatibility
  matrix, migration guide, upgrade guide, and first-hour guide.

This release is intended for private/internal wheel and Docker image reuse. Public PyPI
release hardening remains future work.
