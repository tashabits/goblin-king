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
Phase 8 adds local API users, projects, hashed API tokens, project-scoped access,
audit logs, local rate limits, paginated list responses, and client-quality OpenAPI
metadata.
The final optional phase adds sample proof goblins, a FastAPI-served admin UI for
Docker and Helm deployments, long-running service probes, and an optional Kubernetes
Helm chart. Docker and Compose remain the default local path.

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
curl -H "Authorization: Bearer local-dev-token" http://127.0.0.1:8000/goblins
curl -X POST http://127.0.0.1:8000/jobs \
  -H "Authorization: Bearer local-dev-token" \
  -H "Content-Type: application/json" \
  -d "{\"kind\":\"example.echo\",\"input\":{\"message\":\"hello api\"}}"
```

Create local API principals and scoped tokens:

```bash
goblin-king auth create-user --email dev@example.test --display-name Dev
goblin-king auth create-project --name "Local Project"
goblin-king auth create-token --name local-token --user-id <user-id> --project-id <project-id>
```

Only `GET /health` is open. Other HTTP endpoints and `WS /ws/runs` require bearer
tokens. API list endpoints return paginated envelopes such as:

```bash
curl -H "Authorization: Bearer local-dev-token" "http://127.0.0.1:8000/jobs?limit=20&offset=0"
```

OpenAPI metadata is available at `/openapi.json` with bearer auth schemes, stable
operation IDs, response models, and error response shapes for generated clients.

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

Open the React admin lab bench with Docker Compose:

```bash
make admin-build
make admin-up
open http://127.0.0.1:8080/admin
```

Log in with `local-dev-token`. The admin service serves the same React build in Docker
and Helm, proxies HTTP calls through `/admin-api/*`, and proxies WebSocket run events
through `/admin-ws/runs`. It lists current goblins, worker mappings, jobs, schedules,
runs, fanouts, long-running services, events, heartbeats, artifacts, audit logs, and
rate-limit proof panels.

The tester buttons labeled kill perform King-side cancellation or registered-service
stop actions. They do not hard-kill Docker containers or Kubernetes pods. As the court
scribe says: "A proper goblin returns receipts."

Run the Docker admin proof flow:

```bash
make admin-up
make admin-smoke
```

When the API runs in Docker Compose, the long service is reached at
`http://long-hello:8080` from inside the API container. Override
`LONG_HELLO_URL=http://localhost:8090` only when probing from a host-run API process.

Render the optional Kubernetes chart:

```bash
docker build -t goblin-king:local .
docker build -t goblin-king-example-long-hello:local workers/example.long-hello
make helm-template
```

The Helm chart exposes the admin/API service through an ingress by default using the
`nginx` ingress class. Disable it for deployments that already provide ingress routing:

```bash
helm template goblin-king charts/goblin-king --set admin.ingress.enabled=false
```

For Docker Desktop Kubernetes, install a local ingress controller for port 80 traffic:

```bash
helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx
helm repo update ingress-nginx
helm upgrade --install ingress-nginx ingress-nginx/ingress-nginx \
  --namespace ingress-nginx \
  --create-namespace \
  --set controller.service.type=LoadBalancer \
  --set controller.ingressClassResource.default=true \
  --wait
```

For local ingress access, point `goblin-king.local` at your local Kubernetes ingress
endpoint. On Windows, open Notepad as Administrator, edit
`C:\Windows\System32\drivers\etc\hosts`, and add:

```text
127.0.0.1 goblin-king.local
```

Then browse to `http://goblin-king.local/admin` and log in with `local-dev-token`. If
your local cluster exposes ingress on a different IP, use that IP instead of
`127.0.0.1`.

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
| [User Guide](docs/USER_GUIDE.md) | End-to-end operator and developer guide for Docker, admin UI, sample goblins, API, scheduler, and optional Helm deployment. |
| [Contributing](docs/CONTRIBUTING.md) | Branch, PR, local CI, commenting, goblin documentation, and test expectations. |
| [API Roadmap](docs/api-roadmap.md) | API endpoints deferred beyond Phase 4 and their intended target phases. |
| [Nomena Alignment](docs/nomena-alignment.md) | Notes for adapting Nomena-style queue, worker, heartbeat, and operator proof flows. |

## Current Scope

The current kernel stores durable state in SQLite, schedules due jobs, executes Docker
workers by default, uses Redis as result and live event transport, and exposes a
project-scoped API control plane with local bearer-token auth. It can also discover
goblins from multiple registry files and installed package entry points, queue
mixed-kind fanout batches, create retry jobs from terminal jobs, stream events over
WebSockets, track scheduler/worker heartbeats, audit API activity, and expose
client-oriented OpenAPI metadata. Kubernetes, Redis durability guarantees, and
deployment hardening are optional follow-up work beyond the local Helm proof.
