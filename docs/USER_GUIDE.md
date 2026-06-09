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

When using Goblin King from another project, start with
[Using Goblin King From Another Project](ADOPTING_PROJECTS.md) and keep project imports
inside the [Public API Boundary](PUBLIC_API.md).

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

Start the React admin lab bench with Docker Compose:

```bash
make admin-build
make admin-up
```

Open:

```text
http://127.0.0.1:8080/admin
```

Log in with `local-dev-token`. The same React admin image is used by Docker and Helm.
It lists goblins, worker images, jobs, schedules, runs, fanouts, long-running services,
events, heartbeats, artifacts, audit logs, and rate-limit proof panels. The lab bench
captures request payloads, responses, durable events, and live WebSocket messages. The
King-side kill controls cancel jobs or stop registered services; they do not hard-kill
containers or pods.

For a screenshot walkthrough of each admin panel, see
[Goblin King Admin Guide](ADMIN_GUIDE.md).

Use the Admin/Auth cleanup controls to remove old runtime rows after a testing pass.
Always preview first; removal clears terminal jobs and runs, completed fanouts, captured
events, worker heartbeats, and stopped or unprobed long-service rows. It preserves
schedules, users, projects, API tokens, active jobs, running services, and scheduler
heartbeat.

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
In Docker Compose, register `http://long-hello:8080` from the admin UI because the API
container resolves that service name. In Helm, register `http://goblin-king-long-hello`.
The React admin preloads the correct default from `/admin/config.json` for each
deployment.

## Deploy-Time Discovery Reload

When a host project deploys a new goblin plugin package, registry file, or worker image
map, reload discovery before testing the new kind:

```bash
curl -X POST http://127.0.0.1:8000/admin/discovery/reload \
  -H "Authorization: Bearer local-dev-token"
curl -H "Authorization: Bearer local-dev-token" http://127.0.0.1:8000/admin/discovery/status
```

The React admin has the same flow in the **Discovery** panel. A successful reload
updates the goblin dropdown and worker mapping table at runtime. A failed reload keeps
the previous valid registry active and displays the validation error so the failed
deployment can be fixed safely.

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

When deployed with the default ingress, open:

```text
http://goblin-king.local/admin
```

The admin service proxies API traffic through `/admin-api/*` and live run events through
`/admin-ws/runs`, so the browser uses the same UI paths in Docker and Kubernetes.

For a Docker Desktop Kubernetes smoke test, make sure the cluster can see the locally
built images. Some local clusters use a separate containerd image store from `docker
image ls`. If pods report `ErrImageNeverPull` or appear to use stale `:local` images,
save the rebuilt API/admin/worker images, copy them into a node debug pod under
`/host/tmp`, and import them with:

```bash
chroot /host ctr -n k8s.io images import /tmp/goblin-king-local.tar
chroot /host ctr -n k8s.io images import /tmp/goblin-king-admin-local.tar
chroot /host ctr -n k8s.io images import /tmp/goblin-king-workers.tar
```

The Kubernetes proof flow should include a completed `example.hello` run returning
`Hello World` and two `example.long-hello` probes returning `Hello World from long
running service` with different timestamps.

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
