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

- Split project goblins from core scheduler.
- Support registry files and Python entry point discovery.
- Add example goblin package.
- Document how existing projects can adopt it without taking a hard dependency on implementation details.

### Final Optional Phase: Kubernetes Deployment

- Keep Docker and Compose as the default local/development path.
- Add Kubernetes as an optional runtime/deployment path only for projects that require it.
- Add Helm chart support for API, scheduler, Redis configuration, worker image settings, volumes, secrets, and service exposure.
- Add documentation for when to choose Kubernetes and how to keep local Docker workflows unchanged.
- Add local chart validation and, where practical, kind/minikube smoke tests with explicit PR proof.

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
