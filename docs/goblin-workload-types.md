# Goblin Workload Types

Goblin King is a self-hosted control plane for validated container-backed workloads.
A goblin is a validated container-backed workload managed by Goblin King.

The lifecycle can differ, but the shared model stays the same: container boundary,
validation gate, resource policy, auth scope, and run or service visibility.

## Shared Model

Every goblin has:

- A stable kind or Directory name.
- A configured container image or configured runner container.
- Validation proof before runtime use.
- Resource policy expectations and deployment-level limits.
- Durable status, logs, events, audit records, and operator visibility.
- Project or deployment scope.

Notebook-authored goblins still run inside configured runner containers. They are not
raw local Python execution, and they do not make Python the only worker model.

## Task Goblins

A task goblin is a short-lived container that reads input and context, writes a result
envelope, may write artifacts/logs/events/heartbeats, and exits.

Task goblins are a good fit for scheduled jobs, fanout work, report generation, batch
transforms, maintenance tasks, and other work where completion is the expected final
state.

## Service Goblins

A service goblin is a long-running container-backed workload managed by Goblin King.
Where supported, it becomes ready, answers health/probe/proxy requests, reports status
and logs, and is explicitly stopped or otherwise managed through Goblin King.

Service goblins are not arbitrary unmanaged daemons. They remain project or deployment
scoped, subject to validation and resource policy, and visible through service/admin/API
surfaces.

If a previously registered runtime disappears, a failed proxy connection changes that
exact service record to `failed`, emits `service.proxy_failed`, and returns a stable
unavailable response without exposing resolver or network internals. A later successful
probe can return the same record to `running`; operators can also stop and replace it.

## Notebook Function Goblins

A notebook function goblin is a source-authored Python function bundled from a notebook
or workbook and executed inside the configured notebook function runner container.

Notebook authors may avoid writing a Dockerfile for the function itself, but the
deployment operator still controls the runner image. Validation still applies before the
function goblin can be run.

## Notebook Service Goblins

A notebook service goblin is a source-authored ASGI/FastAPI service bundled from a
notebook or workbook and executed inside the configured notebook service runner
container.

The author provides source, an app symbol, optional inline requirements, a port, and a
probe path. Goblin King validates the bundle through the runner, starts managed Docker
or Kubernetes resources where configured, probes the service, and exposes it through the
managed service path.

## Directory Goblins

A Directory goblin is a deployment-local shared goblin that has been submitted,
validated, reviewed, and published for other authorized users in the same Goblin King
deployment.

The Goblin Directory is intended for teams, classrooms, labs, research groups, and
trusted internal deployments. It is not a public marketplace or public registry.
Approval is a sharing gate, not a security certification. Validation proves contract
compliance, not trustworthiness.

## What Changes Between Workload Types

- Task goblins finish with a run result; service goblins continue until stopped or
  managed.
- Task goblins emphasize result envelopes and artifacts; service goblins emphasize
  readiness, probes, proxy access, and lifecycle state.
- Notebook goblins use operator-controlled runner containers instead of project-written
  Dockerfiles for each authored function or ASGI service.
- Directory goblins add review, publication, discovery by name, and shared invocation
  inside one deployment.

## What Does Not Change

- Goblins remain container-backed workloads.
- Validation remains mandatory before runtime use.
- Resource policy and deployment controls still apply.
- Auth, project scope, and audit proof still matter.
- Operators remain responsible for runner images, secrets, networking, and trust
  boundaries.
- Python helpers are optional authoring conveniences, not the universal worker model.

## Non-Goals

Goblin King does not provide untrusted public container execution, a public app store,
or production-ready public multi-tenant isolation by default. Operators should run only
trusted project images and trusted deployment-local submissions unless they add the
hardening, review, scanning, sandboxing, and governance required for their environment.
