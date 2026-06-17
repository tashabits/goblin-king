# Demo And Doctor Roadmap

This roadmap describes one-command demo and diagnostic flows for Goblin King.
The first local Docker/admin slice is implemented; deeper diagnostic categories
remain planned follow-up work.

Mental model:

```text
Demo proves the happy path. Doctor explains why the happy path is blocked.
```

## Goals

- Provide a demo flow that starts a trusted local stack and proves a sample
  goblin run.
- Provide a doctor flow that diagnoses environment, project, validation, API,
  admin, Redis, Docker, and Compose issues.
- Keep diagnostics actionable and safe.
- Preserve the rule that goblins are contract-compliant OCI/Docker containers.

## Additional Guardrails

- `make demo`, `make doctor`, `make demo-down`, `goblin-king demo up`,
  `goblin-king demo down`, and `goblin-king doctor` are implemented local
  onboarding commands.
- Demo and doctor flows must not mount the Docker socket into goblin task
  containers.
- Doctor output must not treat validation as image trust or security scanning.
- Diagnostics must not weaken the mandatory validation gate.
- The flows must not imply production or public multi-tenant readiness.

## Demo Phase 1: One-Command Demo

Implemented by `goblin-king demo up`.

The command builds/prepares the included adopter stack, starts Docker Compose,
validates `project.inline.hello`, reloads discovery, submits a job through the
admin-proxied API, waits for the scheduler run, and prints the admin URL plus a
short proof summary.

The command should be safe for a trusted local developer machine and should make
cleanup instructions visible.

## Demo Phase 2: Demo Project And Sample Goblin

Partially implemented through `examples/adopting-project`, which uses the same
`GoblinProject` model as adopter projects and proves a small success goblin.
Artifact-producing and controlled-failure admin demo variants remain future
follow-up.

The demo must validate generated or referenced images before scheduling them.

## Doctor Phase 1: Environment Checks

Implemented for Python import, Docker availability, Docker daemon, Docker
Compose, Redis reachability, and admin/API readiness. Docker socket posture,
npm/admin build prerequisites, filesystem storage, and optional Helm tool checks
remain future follow-up.

Checks should report pass, warning, or failure with repair guidance.

## Doctor Phase 2: Project And Validation Checks

Implemented for project config shape, image map coverage, Dockerfile presence,
and validation status. Resolved image identity freshness, effective resource
policy detail, and schedule readiness remain future follow-up.

Doctor should explain that validation proves contract compliance, not whether an
image is safe to trust.

## Doctor Phase 3: Actionable Repair Messages

Implemented repair messages show the likely next command and relevant doc link
for each warning or failure.

Repair messages should avoid vague "check your setup" language and should not
invent commands before they exist.

## Demo And Doctor Phase 4: Closeout And Docs

Close out demo and doctor work by updating README, user guide, adopter guide,
admin guide, proof table, screenshots, and troubleshooting docs.

Closeout proof should include a clean demo run and doctor output for at least
one intentionally broken environment or project setting.

## Non-Goals

- No security scanning or trust certification.
- No untrusted third-party container execution.
- No production hardening claim.
- No federation, remote runners, geographic placement, or distributed
  experiment orchestration.

## Acceptance Criteria

- Demo work has a clear happy-path proof target.
- Doctor work has clear diagnostic categories and repair-output goals.
- Implemented commands are documented as available, while follow-up checks remain
  explicitly future work.
- Docker socket and validation limitations remain explicit.
- The roadmap stays concise and records CLI behavior separately from future
  follow-up diagnostics.
