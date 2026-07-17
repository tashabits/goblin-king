# Changelog

## Unreleased

- Add an opt-in, project-authorized live run-event channel with identical Docker and Kubernetes
  worker contracts, bounded Redis Stream replay, and no changes to existing task payload or status
  shapes.

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
