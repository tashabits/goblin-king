# Demo And Doctor Roadmap

This roadmap describes future one-command demo and diagnostic flows for Goblin
King. It is a planning document, not an implemented feature.

Mental model:

```text
Demo proves the happy path. Doctor explains why the happy path is blocked.
```

## Goals

- Plan a future demo flow that starts a trusted local stack and proves a sample
  goblin run.
- Plan a future doctor flow that diagnoses environment, project, validation,
  API, admin, storage, Redis, Docker, Compose, and optional Helm issues.
- Keep diagnostics actionable and safe.
- Preserve the rule that goblins are contract-compliant OCI/Docker containers.

## Additional Guardrails

- Future demo commands such as `make demo` or `goblin-king demo up` are examples
  only until implemented.
- Demo and doctor flows must not mount the Docker socket into goblin task
  containers.
- Doctor output must not treat validation as image trust or security scanning.
- Diagnostics must not weaken the mandatory validation gate.
- The flows must not imply production or public multi-tenant readiness.

## Demo Phase 1: One-Command Demo

Plan a future command that can build or prepare sample workers, start the local
stack, validate the sample goblin, run it, and print the admin URL plus a short
proof summary.

The command should be safe for a trusted local developer machine and should make
cleanup instructions visible.

## Demo Phase 2: Demo Project And Sample Goblin

Plan a future demo project that uses the same `GoblinProject` model as adopter
projects. The demo should include a small success goblin, an artifact-producing
goblin, and a controlled-failure goblin.

The demo must validate generated or referenced images before scheduling them.

## Doctor Phase 1: Environment Checks

Plan future checks for Docker availability, Docker socket posture, Docker
Compose, required ports, Python environment, npm/admin build prerequisites,
Redis reachability, filesystem storage, and optional Helm tools.

Checks should report pass, warning, or failure with repair guidance.

## Doctor Phase 2: Project And Validation Checks

Plan future checks for project config shape, image map coverage, resolved image
identity, contract version, validation status, stale validation proof, effective
resource policy, and schedule readiness.

Doctor should explain that validation proves contract compliance, not whether an
image is safe to trust.

## Doctor Phase 3: Actionable Repair Messages

Plan repair messages that show the likely next command, relevant doc link, and
expected successful outcome.

Repair messages should avoid vague "check your setup" language and should not
invent commands before they exist.

## Demo And Doctor Phase 4: Closeout And Docs

Close out demo and doctor work by updating README, user guide, adopter guide,
admin guide, proof table, screenshots, and troubleshooting docs.

Closeout proof should include a clean demo run and doctor output for at least
one intentionally broken environment or project setting.

## Non-Goals

- No current implementation of demo or doctor commands in this roadmap.
- No security scanning or trust certification.
- No untrusted third-party container execution.
- No production hardening claim.
- No federation, remote runners, geographic placement, or distributed
  experiment orchestration.

## Acceptance Criteria

- Future demo work has a clear happy-path proof target.
- Future doctor work has clear diagnostic categories and repair-output goals.
- Planned commands are clearly labeled as future examples.
- Docker socket and validation limitations remain explicit.
- The roadmap stays concise and does not change CLI/API/admin behavior.
