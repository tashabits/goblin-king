# Adoption And Onboarding Roadmap

This roadmap describes future work for helping a new trusted adopter move from
clone or vendored checkout to a successful goblin run quickly. It is a planning
document, not an implemented feature.

North star:

```text
A new person can go from clone/vendor to demo to validate to run to admin proof
in under 15 minutes.
```

## Goals

- Make the first successful run feel obvious and low-friction.
- Keep project config as the primary adopter interface.
- Make validation and admin proof part of the first journey.
- Explain Docker, Compose, Redis, storage, and optional Helm requirements early.
- Preserve the container-first model: Goblin King schedules containers, not
  language runtimes.

## Additional Guardrails

- Future onboarding must not imply untrusted third-party container execution is
  supported.
- The mandatory validation gate must not be weakened.
- Validation proves contract compliance, not image trustworthiness.
- Goblin task containers must not receive the Docker socket.
- Python helpers may exist, but Python must not become the required worker
  runtime.
- Future onboarding should be copy-paste friendly without hiding security
  cautions.

## Adoption Phase 1: One-Command Happy Path

Plan a future happy path that can prepare a local demo stack, validate a sample
project goblin, run it, and point the user to the admin proof surface.

The roadmap may discuss future commands, but it must clearly mark them as
planned examples rather than commands that exist today.

## Adoption Phase 2: Better First-Error Experience

Plan clearer first-run errors for missing Docker, unavailable Redis, invalid
project config, missing worker images, failed validation proof, blocked ports,
and admin/API reachability problems.

Errors should include the likely cause, the repair command or doc link, and the
next verification step.

## Adoption Phase 3: Architecture Diagram

Plan a concise adopter-facing diagram that shows the control plane, scheduler,
Redis transport, SQLite storage, admin UI, Docker worker containers, validation
gate, and optional Helm deployment.

The diagram should make the Docker socket boundary explicit: the control plane
may need Docker access in trusted local mode, but goblin task containers must
not receive the Docker socket.

## Adoption Phase 4: Admin Panel Smoke Test

Plan a guided admin smoke test that proves a project goblin is visible,
validated, schedulable, runnable, and inspectable from the existing admin lab.

The smoke test should include job status, run result, artifact metadata where
applicable, event visibility, heartbeat visibility, and validation status.

## Adoption Phase 5: Closeout And Adoption Docs

Close out onboarding work by updating README, adopter docs, screenshots,
troubleshooting guidance, proof table, and roadmap status.

Closeout proof should show the happy path, the first-error path, and the admin
proof path on a clean checkout.

## Non-Goals

- No new runtime model.
- No bypass of validation before scheduling.
- No production multi-tenant hardening claim.
- No untrusted container execution claim.
- No federation, remote runners, geographic placement, or distributed
  experiment orchestration.
- No replacement of existing CLI/project config workflows.

## Acceptance Criteria

- A new adopter has one clear future path from clone/vendor to validated run.
- Planned error handling points to actionable repair steps.
- The admin smoke path proves the goblin in the existing admin surfaces.
- All planned onboarding output keeps the alpha safety posture visible.
- The roadmap remains future-facing and does not claim the planned commands
  exist today.
