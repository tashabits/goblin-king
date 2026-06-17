# Adoption And Onboarding Roadmap

This roadmap tracks work for helping a new trusted adopter move from clone or
vendored checkout to a successful goblin run quickly. The first local demo and
doctor slice is implemented; later onboarding polish remains planned.

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

- Onboarding must not imply untrusted third-party container execution is supported.
- The mandatory validation gate must not be weakened.
- Validation proves contract compliance, not image trustworthiness.
- Goblin task containers must not receive the Docker socket.
- Python helpers may exist, but Python must not become the required worker
  runtime.
- Onboarding should be copy-paste friendly without hiding security cautions.

## Adoption Phase 1: One-Command Happy Path

Implemented in the local demo and doctor slice.

`goblin-king demo up` starts the trusted Docker Compose admin stack with the
included adopter fixture, validates `project.inline.hello`, reloads discovery,
submits a job through the admin-proxied API, waits for the scheduler run, and
prints the admin URL plus `goblin-king demo down` cleanup command.

`goblin-king doctor` checks local prerequisites and current stack state with
pass/warn/fail output and repair commands.

## Adoption Phase 2: Better First-Error Experience

Partially implemented by `goblin-king doctor` for missing Docker, unavailable
Redis, invalid project config, missing worker image coverage, validation proof,
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

Implemented for the included adopter fixture by `goblin-king demo up` plus the
admin runtime audit helper. The demo proves a project goblin is visible,
validated, schedulable, runnable, and inspectable from the existing admin stack.

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

- A new adopter has one clear local path from clone/vendor to validated run.
- Error handling points to actionable repair steps.
- The admin smoke path proves the goblin in the existing admin surfaces.
- All planned onboarding output keeps the alpha safety posture visible.
- The roadmap distinguishes the implemented demo/doctor slice from later planned
  onboarding polish.
