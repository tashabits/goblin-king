# Goblin King

Goblin King is a reusable Python scheduler kernel for running small, injectable worker
modules called goblins. Goblins can run as self-contained Docker workers, which lets each
worker use the language and runtime that fits its job while the King keeps scheduling,
status, and result contracts consistent.

Phase 3 adds Docker execution, worker image configuration, Redis result transport,
Compose deployment helpers, and Makefile targets for local build/test/simulation flows.

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

Use `--runtime in-process` on `jobs submit`, `scheduler run-once`, or `scheduler run`
when debugging trusted local Python goblins without Docker.

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
and write the same result to a mounted fallback file.

## Documentation

| Document | Purpose |
| --- | --- |
| [Goblin King Scheduler Plan](docs/goblin-king-plan.md) | Architecture, phases, contracts, runtime direction, testing plan, and implementation roadmap. |
| [Contributing](docs/CONTRIBUTING.md) | Branch, PR, local CI, commenting, goblin documentation, and test expectations. |

## Current Scope

The current kernel stores durable state in SQLite, schedules due jobs, executes Docker
workers by default, and uses Redis as result transport. The API server, Kubernetes
runtime, fanout endpoints, Redis durability guarantees, and production container
hardening are planned for later phases.
