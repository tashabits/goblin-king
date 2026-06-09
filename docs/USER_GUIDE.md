# Goblin King User Guide

This guide is the operator and developer path after the full Goblin King roadmap is in
place. Docker and Compose are the default local workflow. Kubernetes is optional and is
provided through Helm for projects that need cluster deployment.

## Local Setup

Install dependencies and run local CI:

```bash
python -m pip install -e .[dev]
python -m pytest
python -m ruff check .
```

Build workers and start Redis:

```bash
make deploy
```

## Run A Short Job

Queue and execute the short Hello World sample:

```bash
goblin-king jobs submit example.hello \
  --input examples/input.json \
  --registry examples/goblins.json \
  --images goblin-images.json \
  --redis-url redis://localhost:6379/0
```

The result contains `Hello World` in the structured `GoblinResult` envelope.

## Run The Scheduler

Create a due schedule and run one deterministic scheduler pass:

```bash
goblin-king schedules add example.echo \
  --cron "* * * * *" \
  --input examples/input.json \
  --registry examples/goblins.json \
  --due-now

goblin-king scheduler run-once \
  --registry examples/goblins.json \
  --images goblin-images.json \
  --redis-url redis://localhost:6379/0
```

Inspect persisted state:

```bash
goblin-king jobs list
goblin-king events list --limit 20
goblin-king heartbeats list
```

## Use The API And Admin UI

Start the API:

```bash
goblin-king api run --settings goblin-king-api.json
```

Open the admin UI:

```text
http://127.0.0.1:8000/admin?token=local-dev-token
```

The admin UI is served by the FastAPI process in both Docker and Helm deployments. It
lists goblins, worker images, jobs, long-running services, events, and heartbeats.

The API requires bearer auth for everything except `/health`:

```bash
curl -H "Authorization: Bearer local-dev-token" http://127.0.0.1:8000/goblins
```

## Prove The Long-Running Service

Start the sample long-running service:

```bash
make long-hello-up
```

Register and probe it:

```bash
curl -X POST http://127.0.0.1:8000/services/long-running \
  -H "Authorization: Bearer local-dev-token" \
  -H "Content-Type: application/json" \
  -d "{\"kind\":\"example.long-hello\",\"base_url\":\"http://localhost:8090\"}"

curl -X POST http://127.0.0.1:8000/services/long-running/<service-id>/probe \
  -H "Authorization: Bearer local-dev-token"
```

Each probe returns `Hello World from long running service` with a fresh timestamp.

## Optional Kubernetes Deployment

Render the chart locally:

```bash
make helm-template
```

The chart includes API/admin, scheduler, Redis, persistent storage, and the sample
long-running service. Admin ingress is on by default:

```bash
helm template goblin-king charts/goblin-king
```

Disable ingress when another deployment layer owns routing:

```bash
helm template goblin-king charts/goblin-king --set admin.ingress.enabled=false
```

## Sample Goblins

- `example.hello`: short-running Hello World proof.
- `example.long-hello`: long-running service with timestamped probe responses.
- `example.artifact`: writes a small text artifact and returns artifact metadata.
- `example.environment`: reports safe runtime and context details.
- `example.controlled-failure`: returns a predictable failed result.
- `example.progress`: emits step-style progress data and handoff metadata.

## Pull Request Proof

All project quality gates are local. PRs should include exact `pytest` and `ruff`
output plus any Docker, API, admin UI, Helm, or kind smoke evidence that applies.
