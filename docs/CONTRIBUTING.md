# Contributing

Goblin King code should stay easy to review, test, and reuse across projects.

## Branches And Pull Requests

- Do code work on feature branches. Use the exact branch name requested by the
  maintainer for roadmap phases; do not add prefixes when the plan specifies a branch.
- Submit changes through pull requests into `main`.
- Do not commit directly to `main`.
- Keep each pull request scoped to one coherent change.
- Include a short summary, local CI test evidence, phase objective proof, and any known follow-up work in every pull request.

## Local CI

All phases use local CI unless the project explicitly changes this policy later. Do not
rely on GitHub Actions CI runs as the required quality gate.

Before opening a pull request, run:

```bash
python -m pytest
python -m ruff check .
```

Add any extra manual CLI smoke-test commands to the pull request description when they are relevant.

Pull request bodies must prove that phase objectives were met. For phase work,
include a concrete evidence section that maps each objective to the code, tests,
or manual smoke output that verifies it. Local CI proof should include the exact
commands run and their results, for example `python -m pytest - 25 passed` and
`python -m ruff check . - passed`. When a phase includes CLI behavior, persistence,
or scheduler behavior, include the relevant manual commands and a short statement
of what the command proved.

When a phase includes Docker behavior, PR evidence must include real local Docker proof.
Mocked Docker tests are useful, but they do not replace building the worker image,
running the Docker-backed path, and recording the observed result in the PR body.

Roadmap PRs from `roadmap-proof-preflight` onward must include the browser-driven Docker
and Helm admin runtime audit described in `docs/admin-runtime-audit.md`. The PR body
must include screenshots plus an audit table with each goblin kind, Docker result,
Helm result, run IDs, and notes. Unexpected failed runs are blockers.

When a phase includes API behavior, PR evidence must include local HTTP smoke proof.
At minimum, record a successful read endpoint, a rejected unauthenticated mutation,
an accepted authenticated mutation, and a follow-up read that proves the mutation was
persisted.

When a phase includes reusable package behavior, PR evidence must include generated
package proof. Record the generator command, local editable install, discovery through
`goblin_king.goblins`, worker image build, and a completed scheduler run when the package
includes a worker.

When a phase includes deploy-time discovery reload behavior, PR evidence must include
admin/API reload proof, source and image-map coverage proof, a newly discovered goblin
appearing without a React rebuild, and a failed reload preserving the previous valid
registry.

When a phase includes host-project deployment integration, PR evidence must include
the project validation command, worker build proof, Docker Compose extension proof,
Helm values render proof, discovery reload proof, and admin-visible project goblin
proof.

When a phase includes release or upgrade behavior, PR evidence must include wheel build
proof, compatibility fixture validation, Docker/Helm adoption smoke or render proof,
docs proof for release/migration/upgrade guides, and a compatibility matrix update.

When a phase includes image promotion or deployment orchestration behavior, PR evidence
must include image promotion lifecycle proof, recorded build/push or dry-run command
proof, Helm render or dry-run proof, discovery reload proof, admin deployment panel
proof, and the resulting audit/event records.

When a phase includes fanout or retry behavior, PR evidence must include both API and CLI
proof. Record fanout creation, fanout readback, scheduler execution, retry creation from
a terminal job, and retry completion.

When a phase includes event streaming or heartbeat behavior, PR evidence must include
durable event proof, Redis pub/sub proof, WebSocket proof, scheduler heartbeat proof,
and worker heartbeat proof. Record the command output or HTTP/WebSocket payloads that
show the same work moving through durable history and live streaming paths.

When a phase includes API hardening behavior, PR evidence must include auth/RBAC proof,
project-scope denial proof, token hashing proof, audit-log proof, rate-limit proof,
pagination proof, OpenAPI proof, and Docker regression proof for scheduler execution.

When a phase includes Kubernetes, Helm, admin UI, or long-running service behavior,
PR evidence must include Docker admin proof, Helm render proof, ingress configuration
proof, sample goblin proof, long-running service probe proof with changing timestamps,
and kind/minikube smoke output when a local cluster is available.

## Commenting Standards

- New public modules should start with a concise file-level comment describing purpose and ownership.
- Public functions, runtime entrypoints, goblin contracts, persistence boundaries, and non-obvious helpers should have function-level comments explaining purpose, inputs, outputs, and important failure behavior.
- Avoid comments that restate the code. Comments should explain contract, intent, invariants, edge cases, or operational consequences.
- Each new goblin should document its `GOBLIN_KIND`, expected input shape, result shape, side effects, artifact behavior, and failure modes.

## Tests

Add or update local tests for new contracts, registry behavior, runtime behavior, persistence behavior, and CLI behavior.
Docker runtime phases must also include local Docker tests that fail clearly when Docker
is unavailable.
API phases must include HTTP tests for success paths, auth failures, validation errors,
not-found responses, and persistence effects.
Reusable package phases must include tests for project settings, registry merging, entry
point discovery, template generation, and CLI discovery.
Deploy-time discovery phases must include tests for reload success, reload failure,
source reporting, image-map coverage, scheduler refresh, and admin UI reload controls.
Host-project deployment phases must include tests or render checks for project Compose
fixtures, Helm project values, scheduler project settings, and admin discovery proof
commands.
Release/upgrade phases must include tests for compatibility fixtures, compatibility
matrix shape, release documentation links, and first-hour adoption instructions.
Image promotion/deployment phases must include tests for promotion records, status
updates, Helm render records, CLI commands, admin API endpoints, admin UI controls, and
event/audit proof.
Fanout/retry phases must include tests for batch persistence, derived fanout status, API
auth, CLI commands, retry lineage, rejected live-job retries, and scheduler execution.
Event/heartbeat phases must include tests for event persistence, API event reads,
WebSocket streaming, Redis pub/sub handling, scheduler event emission, worker heartbeat
ingestion, malformed heartbeat handling, and CLI inspection commands.
API hardening phases must include tests for local users/projects/tokens, hashed token
storage, missing/invalid/revoked token failures, project-scope authorization, audit
logging, local rate limits, paginated envelopes, OpenAPI metadata, and existing Docker
execution regressions.
Kubernetes/admin phases must include tests for Helm manifests, ingress defaults,
admin authentication and rendering, sample goblin execution, long-running service
probes, and proof events or audit records.
React admin phases must include frontend tests, frontend build proof, Docker admin UI
proof, Helm admin UI proof, and evidence that each major control-plane path has a
visible tester workflow. Kill controls must be documented as King-side cancellation or
service stop actions, not hard runtime termination.
