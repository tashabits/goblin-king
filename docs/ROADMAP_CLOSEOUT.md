# Production Roadmap Closeout

This closeout records the state after the production, cleanup, container-first worker,
project-adoptable, and adoption-hardening roadmap passes. It is a proof index, not a
future-work plan.

## Implemented Production Items

| Phase | Status | Evidence Location |
| --- | --- | --- |
| 16 Production Kubernetes hardening | Implemented | Helm chart values, templates, and `docs/goblin-king-plan.md`. |
| 17 Redis Streams delivery | Implemented | Event bus, API/CLI stream status, and admin Redis Stream Delivery panel. |
| 18 OIDC authentication | Implemented | OIDC settings, JWT auth tests, and README/User Guide auth notes. |
| 19 Volume artifact management | Implemented | Artifact storage/cleanup API, admin controls, and User Guide. |
| 20 Scoped runtime termination | Implemented | Runtime kill API/CLI/admin controls and termination tests. |
| 21 Image promotion and deployment orchestration | Implemented | Promotion/deployment records, API/CLI/admin controls, and deployment screenshot. |

## Current Proof Surfaces

- README is the top-level user manual with a table of contents and links to deeper docs.
- `docs/USER_GUIDE.md` covers Docker, API, scheduler, admin, Helm, artifacts,
  termination, and deployment proof commands.
- `docs/ADMIN_GUIDE.md` includes screenshots for login, dashboard, goblin lab, task
  board, long services, events, discovery, deployment proof, and cleanup.
- `docs/api-roadmap.md` marks Phases 5-21 as covered and leaves only explicit deferred
  infrastructure items.
- `docs/goblin-king-plan.md` contains the completed production roadmap, cleanup phase,
  appended language/runtime phases, resource-policy work, and adoption hardening notes.
- `docs/project-adoptable-roadmap.md` records the project config path and confirms that
  unvalidated goblins are not schedulable by default. It also records the vendored
  checkout and adopter admin stack passes through Phase 58.
- `docs/proof_table.md` is the complete phase-by-phase proof ledger.
- `docs/testing-your-project-with-the-admin-panel.md` is the short do-this, see-this
  admin proof guide for adopter projects.

## Explicit Deferred Items

- Public PyPI hardening and publication.
- Federation, remote runners, geographic placement, and distributed experiments.
- Production hardening beyond the current trusted self-hosted project scope.
- Untrusted third-party container execution.
- Image signing/scanning.
- Cloud-specific managed service recipes.
- External webhook callbacks.
- Object storage providers beyond Docker volumes and Kubernetes PVCs.
- Identity providers beyond local API tokens and generic OIDC/JWT.
- Native Kubernetes WASI scheduling.
- Official SDKs for every language.
- Deep goblin conformance certification beyond the practical local validator.

## Later Closeouts

- Phase 23: repo-wide code cleanup and module/component splitting.
- Phase 24: formal Goblin Container Contract.
- Phase 25-32: language-agnostic docs, examples, WASI wrappers, runtime proof,
  validation, UI/docs updates, and closeout recorded in
  `docs/language-agnostic-closeout.md`.
- `roadmap-proof-preflight`: proof-table and roadmap hardening plus the repeatable
  Docker/Helm admin runtime audit procedure.
- Phase 34: runtime resource policy enforcement for the gaps documented in
  `docs/goblin-resource-policies.md`.
- Phase 42: project-adoptable alpha closeout completed in
  `docs/project-adoptable-roadmap.md`, with clean-checkout adopter smoke proof
  and final Docker/Helm admin audit evidence.
- Phases 43-48: package-root slimming, behavior-preserving cleanup, mandatory
  scheduling validation, Docker resource-policy proof, project-config hero path, and
  adoption-hardening closeout completed in `docs/project-adoptable-roadmap.md` and
  `docs/proof_table.md`.
- Phases 49-53: layered resource-policy hardening completed project defaults,
  per-goblin overrides, persisted effective policy proof, project-wide concurrency,
  runtime policy mapping, and documentation closeout in
  `docs/goblin-resource-policies.md`, `docs/project-adoptable-roadmap.md`, and
  `docs/proof_table.md`. Release `v0.1.0-alpha.2` records this closeout from synced
  `main`.
- Phases 54-58: vendored checkout docs, adopter admin dev/test stack docs, project
  goblin visibility in the React admin, project admin test quickstart, and adopter stack
  closeout completed the trusted self-hosted project workflow. The admin/API can show,
  validate, submit, and inspect project goblins without rebuilding the React UI.
