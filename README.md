# Goblin King

Goblin King is a reusable Python scheduler kernel for running small, injectable worker
modules called goblins. The project is designed to grow into a Docker-friendly task
scheduler that can queue work, execute goblins, track run status, and collect structured
results for downstream systems.

Phase 2 builds on the library kernel with durable schedules, queued jobs, leases, retry
metadata, timeout bookkeeping, and a scheduler loop that can run due work locally.

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

Run the example goblin:

```bash
goblin-king jobs submit example.echo --input examples/input.json --registry examples/goblins.json
```

Create and run a due schedule:

```bash
goblin-king schedules add example.echo --cron "* * * * *" --input examples/input.json --registry examples/goblins.json --due-now
goblin-king scheduler run-once --registry examples/goblins.json
goblin-king jobs list
```

## Documentation

| Document | Purpose |
| --- | --- |
| [Goblin King Scheduler Plan](docs/goblin-king-plan.md) | Architecture, phases, contracts, runtime direction, testing plan, and implementation roadmap. |
| [Contributing](docs/CONTRIBUTING.md) | Branch, PR, local CI, commenting, goblin documentation, and test expectations. |

## Current Scope

The current kernel intentionally runs goblins in-process and stores metadata in SQLite.
Docker execution, Redis, the API server, container timeout enforcement, and fanout are
planned for later phases.
