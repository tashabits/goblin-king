# Goblin King Scheduler Plan

## Design Principles

The scheduler should start clean and stay reusable. It should provide a small, stable core for scheduling work, dispatching isolated goblin runs, tracking status, and collecting structured results without binding itself to any one application.

The core Goblin King shape:

- A long-running King process blocks on a Redis queue.
- Each job has a `kind`, `id`, `target`, timestamps, and environment metadata.
- The King selects a goblin implementation by kind.
- Work runs in a short-lived Docker container.
- Worker containers receive the job through environment JSON.
- Goblin modules expose a simple Python contract:

```python
GOBLIN_KIND = "backup.full"

def run(job: dict, ctx: Context) -> dict:
    ...
```

- A registry discovers goblin modules from `app/goblins/`.
- Workers publish structured success or error results to Redis.
- Fanout testing queues several jobs and waits for matching result IDs.

This is the MVP kernel. It needs a reusable scheduler layer around it: durable job records, repeat schedules, leases, retries, status transitions, stronger contracts, and pluggable project integration.

The reusable platform pieces:

- A worker registry JSON contract.
- Docker and Kubernetes runtime adapters behind a shared protocol.
- Task states such as `queued`, `launching`, `running`, `stopping`, `completed`, `failed`, `stopped`, and `missing`.
- Task snapshots and task results merged from worker-published messages.
- Redis-backed task state and pub/sub.
- Dynamic job listing from registry rather than hardcoded frontend or API logic.
- Live probes for long-running workers.
- Stop/delete behavior for workers that support it.

The operational options:

- Support both in-process background tasks and Redis-backed durable queue workers.
- Use separate worker settings for different queues.
- Configure worker counts per role.
- Use Docker Compose for local full-stack runs.
- Use cron jobs for recurring scheduled work.

The first implementation should keep the queueing model explicit and controlled by Goblin King. Retry, timeout, queue naming, and worker-count controls should be native scheduler concepts rather than delegated to a hidden task framework.

## Product Goal

Build a reusable Python scheduler that can be dropped into new projects and later adopted by older ones. It should run in Docker, schedule and launch injectable Python goblins, track status, and collect structured outputs that other systems can consume.

The system should feel like this:

1. A project defines goblin modules or goblin packages.
2. A project submits one-off jobs or recurring schedules.
3. The King records the work, leases due jobs, launches isolated goblin execution, and watches status.
4. Each goblin returns a typed result envelope.
5. Results can be read by API, CLI, filesystem, Redis, database, or future scribe/storage integrations.

## Recommended Architecture

```text
goblin-king/
  pyproject.toml
  docker-compose.yml
  Dockerfile
  docs/
    CONTRIBUTING.md
  src/goblin_king/
    api/
    cli.py
    config.py
    contracts.py
    db/
    goblins/
    registry.py
    runtime/
    scheduler.py
    worker_entrypoint.py
  examples/
    goblins/
  tests/
```

Core services:

- `king-api`: FastAPI app for submitting jobs, defining schedules, reading status, and listing goblins.
- `king-scheduler`: long-running scheduler loop that claims due jobs and launches runs.
- `redis`: queue, pub/sub, and optional transient lease backend.
- `postgres` or `sqlite`: durable schedule, job, run, and result storage.
- `goblin-worker`: generic image that imports and runs injectable Python goblin code.

For local development, use SQLite by default to keep the first run simple. Add Postgres as the production/local-full profile once the data model settles.

## Core Domain Model

### Goblin Definition

A goblin is registered by kind and module path:

```json
{
  "kind": "experiment.run",
  "display_name": "Run Experiment",
  "module": "project_goblins.experiment",
  "entrypoint": "run",
  "timeout_seconds": 1800,
  "concurrency_key_template": "experiment:{input.project}:{input.branch}",
  "max_retries": 2,
  "image": "goblin-king-worker:latest"
}
```

### Job

A job is an instruction to do work:

```json
{
  "id": "job_uuid",
  "kind": "experiment.run",
  "input": {},
  "priority": 100,
  "created_at": "2026-06-09T00:00:00Z",
  "created_by": "api",
  "correlation_id": "optional"
}
```

### Schedule

A schedule creates jobs over time:

```json
{
  "id": "schedule_uuid",
  "kind": "experiment.run",
  "input": {},
  "cron": "0 2 * * *",
  "timezone": "America/Vancouver",
  "enabled": true,
  "misfire_policy": "run_once",
  "jitter_seconds": 60
}
```

### Run

A run is one execution attempt:

```json
{
  "id": "run_uuid",
  "job_id": "job_uuid",
  "attempt": 1,
  "status": "running",
  "worker_ref": "container_or_process_id",
  "started_at": "...",
  "finished_at": null
}
```

## Goblin Contract

Use typed Python models at the boundary, while still allowing simple dict ergonomics inside user code.

```python
from goblin_king.contracts import GoblinContext, GoblinResult

GOBLIN_KIND = "example.echo"

def run(input: dict, ctx: GoblinContext) -> GoblinResult:
    return GoblinResult.ok(
        data={"echo": input},
        artifacts=[],
        metrics={"items": 1},
    )
```

Recommended result envelope:

```json
{
  "status": "success",
  "data": {},
  "artifacts": [
    {
      "name": "stdout",
      "uri": "file:///runs/run_uuid/stdout.log",
      "media_type": "text/plain"
    }
  ],
  "metrics": {},
  "handoff": [
    {
      "kind": "scribe.store",
      "payload": {}
    }
  ],
  "error": null
}
```

The important addition is `handoff`: goblins can produce structured messages for the King, future Scribe goblins, or project-specific consumers without knowing where those messages go.

## Runtime Strategy

Start with two runtime adapters:

- `InProcessRuntime`: useful for tests and tiny local scripts.
- `DockerRuntime`: default production path; runs each goblin in an isolated container.

Add later:

- `KubernetesRuntime`: useful when cluster scheduling matters.
- `SubprocessRuntime`: useful for trusted local-only use without Docker.

The Docker worker should receive:

- `GOBLIN_RUN_ID`
- `GOBLIN_KIND`
- `GOBLIN_INPUT_JSON`
- `GOBLIN_REGISTRY_PATH`
- `GOBLIN_RESULTS_BACKEND`
- `GOBLIN_ARTIFACT_ROOT`
- `REDIS_URL`
- `DATABASE_URL`

Avoid passing large payloads through environment variables long term. The MVP can do it for simplicity, but the stable design should pass `run_id` and let the worker fetch input from the database.

## Scheduler Loop

The scheduler should do four things:

1. Materialize due schedules into jobs.
2. Claim queued jobs with a lease.
3. Launch goblin runs through the selected runtime.
4. Reconcile running jobs from worker events and runtime inspection.

Use database rows as the durable source of truth. Redis can help with wakeups, pub/sub, and live notifications, but it should not be the only place a job exists.

Initial statuses:

- `scheduled`
- `queued`
- `leased`
- `launching`
- `running`
- `retrying`
- `completed`
- `failed`
- `cancelled`
- `timed_out`

## API Surface

MVP endpoints:

- `GET /health`
- `GET /goblins`
- `POST /jobs`
- `GET /jobs`
- `GET /jobs/{job_id}`
- `POST /jobs/{job_id}/cancel`
- `POST /schedules`
- `GET /schedules`
- `PATCH /schedules/{schedule_id}`
- `GET /runs/{run_id}`
- `GET /runs/{run_id}/artifacts`

Nice second pass:

- `POST /jobs/fanout`
- `POST /jobs/{job_id}/retry`
- `GET /events`
- `WS /ws/runs`

## CLI Surface

Provide a small CLI so this can be used without a frontend:

```bash
goblin-king init
goblin-king goblins list
goblin-king jobs submit example.echo --input input.json
goblin-king jobs watch <job-id>
goblin-king schedules add nightly-report --cron "0 2 * * *"
goblin-king dev worker example.echo --input input.json
```

## Storage

Tables:

- `goblin_definitions`
- `schedules`
- `jobs`
- `runs`
- `run_events`
- `artifacts`
- `handoffs`

Use SQLAlchemy or SQLModel plus Alembic. Pydantic should own API/contract validation; database models should stay boring and explicit.

## Contribution Guide

Add a contribution guide before implementation work starts. It should live at `docs/CONTRIBUTING.md` and be linked from the README once the README exists.

Required standards:

- All code work happens on feature branches. Use the `codex/` prefix by default for agent-created branches unless a maintainer asks for another naming scheme.
- Changes are submitted through pull requests into `main`. Do not commit directly to `main`.
- PRs should include a short summary, local CI test evidence, phase objective proof, and any known follow-up work.
- Keep implementation PRs scoped to one coherent change. Avoid mixing unrelated refactors with feature work.
- New public modules should start with a concise file-level comment describing purpose and ownership.
- Public functions, runtime entrypoints, goblin contracts, persistence boundaries, and non-obvious helpers should have function-level comments explaining purpose, inputs, outputs, and important failure behavior.
- Avoid noisy comments that restate the code. Comments should explain contract, intent, invariants, edge cases, or operational consequences.
- Each new goblin should document its `GOBLIN_KIND`, expected input shape, result shape, side effects, artifact behavior, and failure modes.
- Tests should accompany new contracts, registry behavior, runtime behavior, persistence behavior, and CLI behavior.

## Docker Compose MVP

Compose services:

- `api`
- `scheduler`
- `redis`
- `worker-build` profile for the generic worker image
- optional `postgres`

The scheduler container needs Docker socket access for local DockerRuntime:

```yaml
volumes:
  - /var/run/docker.sock:/var/run/docker.sock
```

This should be clearly marked as a local/dev adapter. Production should prefer Kubernetes or a restricted worker runner.

## Testing Plan

All phases use local CI unless the project explicitly changes this policy later. Local CI means the developer runs the verification commands on their machine before opening a PR. Do not rely on GitHub Actions CI runs as a required quality gate.

Phase PR bodies must prove that objectives were met with concrete evidence. Include
an objective-by-objective proof section that points to implementation areas,
automated tests, and manual smoke output where relevant. The local CI section must
include the exact commands run and the observed results.

Local CI commands:

```bash
python -m pytest
python -m ruff check .
```

Start with contract tests:

- Goblin registry rejects duplicate kinds.
- Goblin registry rejects missing `run`.
- Result envelope validates success and error shapes.
- Schedule parser handles cron, timezone, disabled schedules, and misfires.
- Scheduler claims each due job once under concurrent scheduler loops.
- DockerRuntime builds correct environment and labels.
- Worker entrypoint publishes success and failure results.

Integration tests:

- Submit one job, run in-process, read completed result.
- Submit fanout jobs and collect all matching result IDs.
- Run one Docker goblin through Compose.
- Retry failed goblin up to configured limit.
- Timeout a long-running goblin.

## MVP Build Phases

### Phase 1: Library Kernel

- Create `pyproject.toml`.
- Add `docs/CONTRIBUTING.md` with branch, PR, comment, and test standards.
- Define Pydantic contracts.
- Implement goblin registry.
- Implement `InProcessRuntime`.
- Implement simple SQLite persistence.
- Add CLI command to run a goblin locally.

### Phase 2: Scheduler

- Add job and schedule tables.
- Implement due schedule materialization.
- Implement job leasing.
- Implement retry and timeout fields.
- Add scheduler loop with clean shutdown.
- Add scheduler CLI commands for schedule creation, schedule listing, one-pass execution, loop execution, and job listing.

### Phase 3: Docker Execution

- Add self-contained worker folders with a `Dockerfile` per worker.
- Build worker images during deployment or local Compose setup.
- Add DockerRuntime as the default runtime while keeping explicit in-process debugging.
- Add worker image map configuration outside the goblin registry.
- Add Redis result transport with file result fallback.
- Add artifact directory convention.
- Add Compose stack and Makefile targets for build, deploy, test, simulate, and clean flows.

### Phase 4: API

- Add FastAPI control plane.
- Add queued job submission and status endpoints.
- Add schedule create/list/patch endpoints.
- Add run and safe artifact endpoints.
- Add static bearer token auth for mutating endpoints.
- Add API settings file, API CLI runner, Compose service, and Makefile smoke target.
- Document uncovered APIs in `docs/api-roadmap.md`.

### Phase 5: Reuse Package

- Add `goblin-king-project.json` for reusable project integration.
- Support multiple registry files and Python entry point discovery.
- Add package/worker template generator.
- Add project validation and project goblin listing CLI commands.
- Allow API goblin discovery from project settings.
- Document how existing projects can adopt it without taking a hard dependency on implementation details.

### Phase 6: Fanout And Retry

- Add durable fanout batches with mixed-kind child jobs.
- Add fanout API and CLI create/list/show flows.
- Add retry API and CLI commands that create fresh queued jobs from terminal jobs.
- Derive fanout status from child jobs and runs.
- Keep fanout and retry queue-only; scheduler execution remains separate.

### Phase 7: Events, Streaming, And Heartbeats

- Add durable SQLite event history for API, scheduler, runtime, and worker activity.
- Publish event envelopes through Redis pub/sub for live subscribers.
- Add WebSocket run event streaming with `WS /ws/runs`.
- Add scheduler and worker heartbeat persistence and read APIs.
- Extend Docker worker contracts so workers publish heartbeat envelopes while running.
- Keep production auth, pagination hardening, and OpenAPI customization deferred to Phase 8.

### Phase 8: Production API Hardening

- Add local SQLite users, teams, projects, memberships, hashed API tokens, and role grants.
- Require bearer-token authentication for all non-health API and WebSocket surfaces.
- Enforce project-scoped access for jobs, schedules, fanouts, runs, events, and artifacts.
- Add audit logs for mutations, auth failures, token operations, and rate-limit denials.
- Add SQLite-backed local rate limits for deterministic development proof.
- Add paginated/filterable list envelopes and client-quality OpenAPI response contracts.

### Final Optional Phase: Kubernetes And Admin Proof

- Keep Docker and Compose as the default local/development path.
- Add Kubernetes as an optional runtime/deployment path only for projects that require it.
- Add Helm chart support for API, scheduler, Redis configuration, worker image settings, volumes, secrets, service exposure, and admin UI exposure.
- Include an optional Helm ingress for the admin/API service. It defaults on and can be disabled with `admin.ingress.enabled=false` when a deployment already supplies ingress.
- Add a web admin interface for both Docker and Kubernetes deployments that reads the current goblin list, lets operators spawn goblins, captures inbound and outbound traffic plus messaging, and proves deployed goblins work.
- Add a short-running `example.hello` job that returns `Hello World`.
- Add a long-running `example.long-hello` service that returns `Hello World from long running service` plus a fresh timestamp on each probe.
- Add illustrative artifact, environment, controlled-failure, and progress goblins for admin validation and demos.
- Add a user guide that explains the complete project workflow after all phases.
- Add documentation for when to choose Kubernetes and how to keep local Docker workflows unchanged.
- Add local chart validation and, where practical, kind/minikube smoke tests with explicit PR proof.

## Project-Ready Adoption Roadmap Extension

The current roadmap gets Goblin King to a reusable scheduler, API, Docker/Kubernetes
runtime, and admin proof surface. The next roadmap extension makes it practical for
another project to install Goblin King as an internal dependency, define project-owned
goblins, deploy them, and have the admin/API discover those goblin types without
rebuilding the React admin.

Primary direction:

- Host projects install Goblin King as an internal Python wheel and consume published
  API/scheduler/admin Docker images.
- Host projects provide goblins as plugin packages with `goblin_king.goblins` entry
  points, optional registry files, worker folders, and worker image maps.
- Deploy-time discovery supports runtime reload so newly deployed goblin definitions
  appear in the API/admin without restarting the React admin.
- Public PyPI publishing is deferred; the first target is private/internal package and
  image reuse.

### Phase 11: Stable Internal Package Boundary

- Define the supported public Python API surface for adopting projects.
- Expand `goblin_king.__init__` exports to include stable adoption primitives only:
  contracts, registry/project settings helpers, worker image map loading, and app or
  scheduler factory entrypoints where appropriate.
- Document public, semi-public, and internal modules so host projects know which imports
  are compatible across internal releases.
- Add internal wheel versioning policy and compatibility notes for goblin contracts,
  registry schemas, worker image maps, API settings, and worker contract versions.
- Add a "use from another project" guide covering editable install, wheel install,
  Docker image usage, and where host projects should put goblin packages.

### Phase 12: Project Plugin SDK And Templates

- Expand the package generator into a project plugin SDK path.
- Generated plugins include Python package metadata, a `goblin_king.goblins` entry point,
  registry stub, worker image map stub, a short-running worker folder with Dockerfile,
  optional long-running service worker folder with Dockerfile, local tests, and README
  integration instructions.
- Add project-level examples for multiple goblin packages in one adopting repo.
- Add validation commands for plugin metadata, entry-point discovery, duplicate kinds,
  worker image map coverage, worker Dockerfiles, and local worker buildability.
- Update project adoption docs to map existing queue workers into goblin kinds,
  inputs, results, heartbeats, artifacts, and handoffs.

### Phase 13: Deploy-Time Discovery And Runtime Reload

- Status: implemented in Phase 13.
- Add runtime reload support for project settings, registry files, entry points, and
  worker image maps.
- Add authenticated admin discovery endpoints: `POST /admin/discovery/reload`,
  `GET /admin/discovery/status`, and `GET /admin/discovery/sources`.
- Reload updates in-memory API registry and worker image map state safely; failed reloads
  preserve the previous valid registry and report validation errors.
- Scheduler reloads through the same discovery version marker through an explicit reload
  hook.
- Add an admin Discovery panel showing loaded sources, entry-point goblins, image map
  coverage, rejected definitions, duplicate kind errors, last reload time, current
  discovery version, and a reload button.
- Admin continues to read goblins dynamically through the API; no React rebuild is needed
  for newly deployed goblin types.

### Phase 14: Host Project Deployment Integration

- Status: implemented in Phase 14.
- Add deployment conventions for installing project plugin wheels into API/scheduler
  images and mounting or baking project registry/image-map files.
- Add Docker Compose extension examples for a host project that uses Goblin King services
  plus project-specific worker images.
- Add Helm values patterns for extra project registries, worker image map entries,
  long-running service workers, project package images, and post-upgrade discovery reload.
- Add Makefile/documented commands for building project goblin packages, building project
  worker images, starting the stack, reloading discovery, and running admin proof.
- Prove that a newly deployed project goblin appears in admin after reload and can be
  spawned without frontend rebuild.

### Phase 15: Project-Ready Release And Upgrade Story

- Add an internal release checklist for building the wheel, API/scheduler/admin Docker
  images, sample plugin package, local CI, Docker adoption smoke, and Helm adoption smoke.
- Add upgrade compatibility tests using a sample adopting-project fixture.
- Add docs for migrating existing project scripts/workers into goblin plugins.
- Add a changelog and compatibility matrix for goblin contract version, registry schema
  version, worker contract version, and API compatibility.
- Add a "first hour with Goblin King in your project" guide: install the wheel, generate
  a plugin, define a goblin, build the worker image, start the stack, reload discovery,
  spawn the goblin from admin, and inspect run/events/heartbeats/artifacts.

## Key Decisions To Make Before Coding

1. Database default: SQLite-only MVP, or Postgres in Compose from day one.
2. Runtime default: Docker-only MVP, or include in-process runtime immediately for fast tests.
3. Registry source: Python package discovery, JSON registry, or both.
4. Result storage: database rows only, filesystem artifacts, or optional object storage abstraction.
5. Schedule syntax: cron only, or cron plus interval/date triggers.

My recommendation: SQLite plus in-process runtime for Phase 1, DockerRuntime in Phase 3, JSON registry plus Python module discovery, cron plus one-off jobs, and filesystem artifacts with database metadata.

## First Implementation Slice

Build the smallest useful vertical path:

1. Define `GoblinContext`, `GoblinResult`, `GoblinDefinition`, `JobRecord`, and `RunRecord`.
2. Add `examples/goblins/echo.py`.
3. Implement registry discovery from a JSON file.
4. Implement `goblin-king jobs submit example.echo --input examples/input.json`.
5. Execute through `InProcessRuntime`.
6. Store run result in SQLite.
7. Add one test that submits a job and asserts a completed result.

That gives us a working spine before adding Docker, API, cron, retries, and fanout.
