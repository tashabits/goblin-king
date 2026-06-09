# Goblin King

Goblin King is a reusable Python scheduler kernel for running small, injectable worker
modules called goblins. The project is designed to grow into a Docker-friendly task
scheduler that can queue work, execute goblins, track run status, and collect structured
results for downstream systems.

Phase 1 builds the library kernel: typed goblin contracts, JSON registry loading,
in-process execution, SQLite-backed job/run persistence, example goblins, a CLI, and
local tests.

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

## Documentation

| Document | Purpose |
| --- | --- |
| [Goblin King Scheduler Plan](docs/goblin-king-plan.md) | Architecture, phases, contracts, runtime direction, testing plan, and implementation roadmap. |
| [Contributing](docs/CONTRIBUTING.md) | Branch, PR, local CI, commenting, goblin documentation, and test expectations. |

## Current Scope

The current kernel intentionally runs goblins in-process and stores metadata in SQLite.
Docker execution, Redis, the API server, cron scheduling, leases, retries, and fanout are
planned for later phases.
