# Goblin King

Goblin King is a reusable Python scheduler kernel for running small, injectable worker
modules called goblins. Goblins can run as self-contained Docker workers, which lets each
worker use the language and runtime that fits its job while the King keeps scheduling,
status, and result contracts consistent.

Phase 4 adds a FastAPI control plane for discovering goblins, queueing jobs, managing
schedules, inspecting runs, and serving local artifacts safely.
Phase 5 adds reusable project integration through project settings, multiple registry
files, Python package entry point discovery, and a package/worker template generator.
Phase 6 adds durable fanout batches and retry APIs/CLI commands for queueing related
work without executing it inline.
Phase 7 adds durable event history, Redis pub/sub streaming, WebSocket run updates,
and scheduler/worker heartbeat tracking.

## Quick Start

Install the package for local development:

```bash
python -m pip install -e .[dev]
```

Run the local CI checks:

```bash
python -m pytest
python -m ruff check .
```

Build worker images and start Redis:

```bash
make deploy
```

Create and run a due schedule through Docker:

```bash
goblin-king schedules add example.echo --cron "* * * * *" --input examples/input.json --registry examples/goblins.json --due-now
goblin-king scheduler run-once --registry examples/goblins.json --images goblin-images.json --redis-url redis://localhost:6379/0
goblin-king jobs list
```

Or run the local simulation target:

```bash
make simulate
```

Run the API locally:

```bash
goblin-king api run --settings goblin-king-api.json
```

Smoke the API from another terminal:

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/goblins
curl -X POST http://127.0.0.1:8000/jobs \
  -H "Authorization: Bearer local-dev-token" \
  -H "Content-Type: application/json" \
  -d "{\"kind\":\"example.echo\",\"input\":{\"message\":\"hello api\"}}"
```

Use `--runtime in-process` on `jobs submit`, `scheduler run-once`, or `scheduler run`
when debugging trusted local Python goblins without Docker.

Create a reusable goblin package skeleton:

```bash
goblin-king project init-package ./my-goblin --kind my.echo --package-name my_echo --image my-echo:local
python -m pip install -e ./my-goblin
goblin-king project validate --project goblin-king-project.json
goblin-king project goblins list --project goblin-king-project.json
```

Project integration settings live in `goblin-king-project.json` and can combine JSON
registry files with installed Python package entry points from `goblin_king.goblins`.

Queue a fanout batch and inspect it:

```bash
goblin-king jobs fanout --input fanout.json --registry examples/goblins.json
goblin-king fanouts list
goblin-king fanouts show <fanout-id>
```

Retry a terminal job:

```bash
goblin-king jobs retry <job-id> --reason "try again"
```

Inspect event history and heartbeats after scheduler work:

```bash
goblin-king events list --limit 20
goblin-king heartbeats list
```

Watch live event envelopes from Redis:

```bash
goblin-king events watch --redis-url redis://localhost:6379/0
```

The API exposes the same event data over `GET /events`, scheduler and worker liveness
over `GET /heartbeats`, and live run updates over `WS /ws/runs`.

## Worker Images

Each Docker worker lives in a self-contained folder with its own `Dockerfile`.
Worker build settings live in `goblin-images.json`:

```json
{
  "workers": {
    "example.echo": {
      "context": "workers/example.echo",
      "dockerfile": "Dockerfile",
      "image": "goblin-king-example-echo:local"
    }
  }
}
```

Workers receive JSON input/context files, publish a `GoblinResult` envelope to Redis,
write the same result to a mounted fallback file, and publish heartbeat envelopes while
they run. The Docker runtime records those heartbeats in SQLite and mirrors event
updates to Redis pub/sub for live subscribers.

## Documentation

| Document | Purpose |
| --- | --- |
| [Goblin King Scheduler Plan](docs/goblin-king-plan.md) | Architecture, phases, contracts, runtime direction, testing plan, and implementation roadmap. |
| [Contributing](docs/CONTRIBUTING.md) | Branch, PR, local CI, commenting, goblin documentation, and test expectations. |
| [API Roadmap](docs/api-roadmap.md) | API endpoints deferred beyond Phase 4 and their intended target phases. |

## Current Scope

The current kernel stores durable state in SQLite, schedules due jobs, executes Docker
workers by default, uses Redis as result and live event transport, and exposes a
local/dev API control plane. It can also discover goblins from multiple registry files
and installed package entry points, queue mixed-kind fanout batches, create retry jobs
from terminal jobs, stream events over WebSockets, and track scheduler/worker
heartbeats. Kubernetes, Redis durability guarantees, production auth, and deployment
hardening are planned for later phases.
