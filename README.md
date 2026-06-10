# Goblin King

Goblin King is a reusable scheduler and control plane for running small, isolated worker
containers called goblins. Goblins run as self-contained Docker/OCI workers, which lets
each worker use the language and runtime that fits its job while the King keeps
scheduling, status, and result contracts consistent.

The current implementation covers the Phase 1-24 roadmap: a SQLite-backed scheduler,
Docker worker execution, FastAPI control plane, reusable project/plugin discovery,
fanout and retry workflows, durable events, Redis pub/sub and Redis Streams delivery,
WebSocket run updates, scheduler and worker heartbeats, local bearer-token auth,
optional OIDC/JWT bearer auth, volume/PVC-backed artifact management, scoped runtime
termination, project scoping, audit/rate-limit proof, a Docker/Helm admin UI,
deploy-time discovery reload, host-project adoption examples, internal release/upgrade
checks, cloud-neutral Helm hardening, image promotion/deployment proof records,
repo-wide cleanup, and the formal Goblin Container Contract. Docker and Compose remain
the default local path; Kubernetes is optional through the Helm chart.

## Table Of Contents

- [Goblin King](#goblin-king)
- [Table Of Contents](#table-of-contents)
- [Quick Start](#quick-start)
- [Goblin Container Contract](#goblin-container-contract)
- [Worker Images](#worker-images)
- [Image Promotion And Deployment Proof](#image-promotion-and-deployment-proof)
- [Documentation](#documentation)
- [Current Scope](#current-scope)
- Deeper manuals:
  - [User Guide](docs/USER_GUIDE.md)
  - [Admin Guide](docs/ADMIN_GUIDE.md)
  - [Goblin Container Contract](docs/goblin-container-contract.md)
  - [What Is A Goblin?](docs/what-is-a-goblin.md)
  - [Writing Goblins](docs/writing-goblins.md)
  - [Goblin Dockerfiles](docs/goblin-dockerfiles.md)
  - [Language-Agnostic Workers](docs/language-agnostic-workers.md)
  - [Security Model](docs/security-model.md)
  - [API Roadmap](docs/api-roadmap.md)
  - [Scheduler Plan](docs/goblin-king-plan.md)
  - [Adopting Projects](docs/ADOPTING_PROJECTS.md)
  - [Public API Boundary](docs/PUBLIC_API.md)
  - [Release Checklist](docs/RELEASE_CHECKLIST.md)
  - [Production Roadmap Closeout](docs/ROADMAP_CLOSEOUT.md)
  - [Code Cleanup Notes](docs/CODE_CLEANUP.md)

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

For external identity, enable OIDC/JWT validation in `goblin-king-api.json`. Local API
tokens are checked first; if no local token matches, Goblin King validates the bearer
token against the configured issuer, audience, and JWKS URL, then maps configured role
and project claims into the same RBAC model:

```json
{
  "oidc": {
    "enabled": true,
    "issuer": "https://issuer.example",
    "audience": "goblin-king",
    "jwks_url": "https://issuer.example/.well-known/jwks.json",
    "role_claim": "goblin_king_role",
    "project_claim": "goblin_king_project_id"
  }
}
```

OpenAPI metadata is available at `/openapi.json` with bearer auth schemes, stable
operation IDs, response models, and error response shapes for generated clients.

Use `--runtime in-process` on `jobs submit`, `scheduler run-once`, or `scheduler run`
when debugging trusted local Python goblins without Docker. In-process execution is a
developer convenience; the worker model is the container contract.

Create a reusable goblin package skeleton:

```bash
goblin-king project init-package ./my-goblin --kind my.echo --package-name my_echo --image my-echo:local
python -m pip install -e ./my-goblin
goblin-king project validate --project goblin-king-project.json
goblin-king project goblins list --project goblin-king-project.json
```

Project integration settings live in `goblin-king-project.json` and can combine JSON
registry files with installed Python package entry points from `goblin_king.goblins`.
After deploying a new project plugin or worker image map, reload discovery through the
admin UI or API:

```bash
curl -X POST http://127.0.0.1:8000/admin/discovery/reload \
  -H "Authorization: Bearer local-dev-token"
curl -H "Authorization: Bearer local-dev-token" http://127.0.0.1:8000/admin/discovery/status
curl -H "Authorization: Bearer local-dev-token" http://127.0.0.1:8000/admin/discovery/sources
```

Failed reloads leave the previous valid registry active and report validation errors
for the Discovery panel. New goblin kinds are read from the API at runtime, so the
React admin does not need a rebuild when project goblins are added.

## Goblin Container Contract

Every goblin is a contract-compliant OCI/Docker container. Goblin King schedules
containers, not language runtimes. The language inside the container is an
implementation detail, and Python helpers are optional conveniences rather than a
requirement.

The canonical worker interface is
[Goblin Container Contract](docs/goblin-container-contract.md). It defines required
environment variables, mounted input/context/result/artifact paths, result envelopes,
stdout/stderr behavior, progress/events, heartbeats, exit codes, timeouts,
cancellation, security expectations, and the container-wrapped WASI/WebAssembly model.

Minimal contract-only hello goblins live under `examples/goblins/hello-*` for Go,
Rust, Node.js, Java, .NET/C#, Ruby, PHP, shell, and Python. These are standalone
container examples first; the Goblin King registry/runtime proof for them lands in
the later cross-language runtime phase.

Container-wrapped WASI examples live under `examples/goblins/wasi-*`. They still
build and run as normal containers: the container entrypoint invokes Wasmtime and
the `.wasm` module reads/writes the same contract files.

To run the cross-language and WASI examples through Goblin King's Docker runtime,
use the dedicated registry/image map:

```bash
make run-cross-language-proof
```

That target builds the example images from `examples/cross-language-images.json`
and submits every kind in `examples/cross-language-goblins.json`.

Contract behavior examples live under `examples/goblins/behavior-*` and are wired
through `examples/behavior-goblins.json` plus `examples/behavior-images.json`.
They cover artifact metadata, progress/logging, controlled failure, input
transform/context reading, timeout-ish cancellation-friendly work, and a WASI
context sample.

Use [Goblin Contract Validation](docs/goblin-contract-validation.md) to build and
run worker images with temporary contract mounts:

```bash
python -m goblin_king.cli workers validate \
  --registry examples/cross-language-goblins.json \
  --images examples/cross-language-images.json \
  --input examples/cross-language-input.json \
  --build \
  --require-success
```

For host-project deployment integration, see
`examples/adopting-project/`. It includes:

- `docker-compose.host-project.yml` for layering project workers and project settings
  over the base Docker Compose stack.
- `helm-values.yaml` for mounting project config, passing scheduler `--project`, and
  adding project long-running services.
- Makefile proof targets:
  - `make project-validate`
  - `make project-build-workers`
  - `make project-discovery-reload`
  - `make project-admin-proof`

For the project-ready release and upgrade story, start with the
[First Hour Guide](docs/FIRST_HOUR.md), [Release Checklist](docs/RELEASE_CHECKLIST.md),
[Compatibility Matrix](docs/COMPATIBILITY.md), [Upgrade Guide](docs/UPGRADING.md), and
[Migration Guide](docs/MIGRATION_GUIDE.md).

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

Inspect Redis Streams delivery health and read stream events through a consumer group:

```bash
goblin-king events stream-status --redis-url redis://localhost:6379/0
goblin-king events stream-read --redis-url redis://localhost:6379/0 --ack
```

The API exposes the same durable SQLite event data over `GET /events`, Redis Stream
transport health over `GET /events/stream/status`, scheduler and worker liveness over
`GET /heartbeats`, and live run updates over `WS /ws/runs`. SQLite remains the durable
source of truth; Redis pub/sub is the live rail and Redis Streams provide replayable
delivery for operators and integrations.

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
rate-limit proof panels. The Discovery panel reloads deploy-time goblin sources and
shows registry files, entry-point usage, worker image-map coverage, rejected
definitions, and the current discovery version. The Admin/Auth panel also has cleanup controls for old
runtime rows: preview first, then remove terminal jobs/runs, completed fanouts,
captured events, worker heartbeats, and stopped or unprobed long-service rows while
leaving schedules, auth/project data, active jobs, running services, and scheduler
heartbeat intact.

The Runs & Artifacts panel reports the configured artifact volume/PVC root, file count,
total bytes, metadata rows, and writable status. Admins can preview and execute artifact
cleanup without moving bytes to object storage; Docker uses the `goblin-king-data`
Compose volume and Helm uses the chart PVC.

The tester buttons labeled kill perform King-side cancellation. The hard-kill runtime
buttons are separate admin controls and only target Docker containers or Kubernetes Jobs
carrying Goblin King labels such as `goblin-king.worker=true`,
`goblin-king.job-id=<id>`, and `goblin-king.run-id=<id>`. Registered long-running
services use a hard-stop presentation control because they are registered by URL rather
than owned as runtime rows.

Run the Docker admin proof flow:

```bash
make admin-up
make admin-smoke
```

When the API runs in Docker Compose, the long service is reached at
`http://long-hello:8080` from inside the API container. Override
`LONG_HELLO_URL=http://localhost:8090` only when probing from a host-run API process.
The React admin reads `/admin/config.json` from its container at startup, so Docker
prefills `http://long-hello:8080` and Helm prefills `http://goblin-king-long-hello`.

Render the optional Kubernetes chart:

```bash
docker build -t goblin-king:local .
python -m goblin_king.cli workers build --images goblin-images.json
docker build -t goblin-king-admin-ui:local admin-ui
docker build -t goblin-king-example-long-hello:local workers/example.long-hello
make helm-template
```

The Helm chart exposes the admin/API service through an ingress by default using the
`nginx` ingress class. Disable it for deployments that already provide ingress routing:

```bash
helm template goblin-king charts/goblin-king --set admin.ingress.enabled=false
```

The chart also includes cloud-neutral production controls for teams that need a more
formal cluster deployment without changing Docker Compose as the default local path:

- API, scheduler, and admin resource requests/limits, autoscaling, disruption budgets,
  node selectors, tolerations, and affinity.
- Shared pod/container security contexts and image pull secrets.
- Configurable PVC access modes, storage class, and size for SQLite/artifacts.
- Optional NetworkPolicy.
- Ingress class, annotations, path type, and TLS blocks.
- `api.existingSecret` for externally managed bootstrap credentials.

For example:

```bash
helm template goblin-king charts/goblin-king \
  --set api.existingSecret=goblin-king-secrets \
  --set api.autoscaling.enabled=true \
  --set admin.ingress.tls[0].secretName=goblin-king-tls \
  --set admin.ingress.tls[0].hosts[0]=goblin-king.local \
  --set networkPolicy.enabled=true
```

External secret controllers, managed ingress details, storage classes, and registry
credentials remain deployment-specific choices; the chart exposes neutral hooks for
those systems instead of assuming one cloud.

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

Docker Desktop Kubernetes may use a separate containerd image store from the Docker
CLI image list. If Helm pods report `ErrImageNeverPull` or keep running an older
`:local` image, import the rebuilt images into the worker node before the Helm smoke:

```powershell
docker save goblin-king:local -o goblin-king-local.tar
docker save goblin-king-admin-ui:local -o goblin-king-admin-local.tar
docker save goblin-king-example-hello:local goblin-king-example-echo:local `
  goblin-king-example-progress:local goblin-king-example-artifact:local `
  goblin-king-example-environment:local goblin-king-example-controlled-failure:local `
  -o goblin-king-workers.tar
kubectl debug node/desktop-worker --image=busybox -- sleep 600
kubectl cp .\goblin-king-local.tar <debug-pod>:/host/tmp/goblin-king-local.tar
kubectl cp .\goblin-king-admin-local.tar <debug-pod>:/host/tmp/goblin-king-admin-local.tar
kubectl cp .\goblin-king-workers.tar <debug-pod>:/host/tmp/goblin-king-workers.tar
kubectl exec <debug-pod> -- chroot /host ctr -n k8s.io images import /tmp/goblin-king-local.tar
kubectl exec <debug-pod> -- chroot /host ctr -n k8s.io images import /tmp/goblin-king-admin-local.tar
kubectl exec <debug-pod> -- chroot /host ctr -n k8s.io images import /tmp/goblin-king-workers.tar
```

The Helm admin proof should show both a completed short `example.hello` job with
`Hello World` and a long-running `example.long-hello` service probe whose timestamp
changes between probes.

## Worker Images

Each goblin worker lives in a self-contained folder with its own `Dockerfile`.
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

Workers receive JSON input/context files, publish a `GoblinResult`-shaped envelope to
Redis, write the same result to a mounted fallback file, and publish heartbeat envelopes
while they run. The worker can be written in any language that obeys the
[Goblin Container Contract](docs/goblin-container-contract.md).

## Image Promotion And Deployment Proof

Phase 21 adds generic promotion and deployment proof records. These records do not tie
Goblin King to one cloud registry or deployment platform. They capture the image, target
tag, planned build/push commands, Helm render command, discovery reload result, audit
logs, and events that prove an operator action happened.

Plan and mark a worker image promotion from the CLI:

```bash
goblin-king deploy promotions plan example.hello \
  --target-image registry.example/goblin-king-example-hello:prod \
  --images goblin-images.json --build --push
goblin-king deploy promotions list
goblin-king deploy promotions mark <promotion-id> --status promoted --digest sha256:...
```

Record Helm render intent without applying anything:

```bash
goblin-king deploy helm-template --chart charts/goblin-king --release goblin-king
goblin-king deploy records
```

The React admin includes **Image Promotion & Deploy**. It shows worker image coverage,
plans dry-run image promotion, records Helm render intent, reloads discovery after a
deployment, and displays the deployment proof trail. The King loves ambition, but he
requires receipts.

## Documentation

| Document | Purpose |
| --- | --- |
| [Goblin King Scheduler Plan](docs/goblin-king-plan.md) | Architecture, phases, contracts, runtime direction, testing plan, and implementation roadmap. |
| [User Guide](docs/USER_GUIDE.md) | End-to-end operator and developer guide for Docker, admin UI, sample goblins, API, scheduler, and optional Helm deployment. |
| [Admin Guide](docs/ADMIN_GUIDE.md) | Screenshot walkthrough for logging in, spawning goblins, watching tasks, probing long services, reading events, and cleaning old rows. |
| [Goblin Container Contract](docs/goblin-container-contract.md) | Canonical language-agnostic worker container contract. |
| [What Is A Goblin?](docs/what-is-a-goblin.md) | Plain-language model of goblins as short-lived container tasks. |
| [Writing Goblins](docs/writing-goblins.md) | Practical steps for building a contract-compliant goblin. |
| [Goblin Dockerfiles](docs/goblin-dockerfiles.md) | Minimal, multi-stage, non-root, read-only, and WASI wrapper Dockerfile patterns. |
| [Language-Agnostic Workers](docs/language-agnostic-workers.md) | Guidance for writing goblins in any container-packaged runtime. |
| [Goblin Examples Index](docs/examples-index.md) | Cross-language, WASI, and behavior sample goblins with proof commands. |
| [Choose Your Runtime](docs/choose-your-runtime.md) | Runtime comparison guide for picking a worker language. |
| [Goblin Contract Validation](docs/goblin-contract-validation.md) | Local validation command for image builds, result envelopes, and artifacts. |
| [Language-Agnostic Closeout](docs/language-agnostic-closeout.md) | Audit summary for the container-first worker phases and remaining deferrals. |
| [Security Model](docs/security-model.md) | Honest container security expectations and runtime hardening guidance. |
| [Public API Boundary](docs/PUBLIC_API.md) | Stable root imports, semi-public commands, internal modules, and internal wheel compatibility policy. |
| [Adopting Projects](docs/ADOPTING_PROJECTS.md) | How another project installs Goblin King, defines goblin plugins, builds workers, and proves the integration. |
| [First Hour Guide](docs/FIRST_HOUR.md) | Fast path from internal install to first project goblin run. |
| [Release Checklist](docs/RELEASE_CHECKLIST.md) | Internal wheel, Docker image, local CI, Docker adoption, and Helm proof checklist. |
| [Production Roadmap Closeout](docs/ROADMAP_CLOSEOUT.md) | Phase 16-21 closeout audit, current proof surfaces, and explicit deferred items. |
| [Code Cleanup Notes](docs/CODE_CLEANUP.md) | Phase 23 before/after cleanup summary and helper-module rules. |
| [Compatibility Matrix](docs/COMPATIBILITY.md) | Contract and schema compatibility versions for project-ready adoption. |
| [Upgrade Guide](docs/UPGRADING.md) | Host-project upgrade procedure and compatibility fixture policy. |
| [Migration Guide](docs/MIGRATION_GUIDE.md) | How to move existing scripts and workers into goblin plugins. |
| [Contributing](docs/CONTRIBUTING.md) | Branch, PR, local CI, commenting, goblin documentation, and test expectations. |
| [API Roadmap](docs/api-roadmap.md) | API endpoints deferred beyond Phase 4 and their intended target phases. |
| [Project Adoption](docs/project-adoption.md) | Notes for adapting existing queue, worker, heartbeat, and operator proof flows. |

## Current Scope

The current kernel stores durable state in SQLite, schedules due jobs, executes Docker
workers by default, uses Redis as result and live event transport, and exposes a
project-scoped API control plane with local bearer-token auth. It can also discover
goblins from multiple registry files and installed package entry points, queue
mixed-kind fanout batches, create retry jobs from terminal jobs, stream events over
WebSockets, track scheduler/worker heartbeats, audit API activity, and expose
client-oriented OpenAPI metadata. It now documents a stable internal package boundary
for adopting projects and internal wheel reuse, with a plugin SDK path for short-running
and long-running goblin workers. Redis Streams provide replayable delivery proof for
event consumers, and the optional Helm chart includes cloud-neutral production
hardening controls. Artifact bytes stay on Docker volumes and Kubernetes PVCs with
status and cleanup APIs. Scoped hard runtime termination is available for
Goblin-labeled Docker and Kubernetes runtime objects. Image promotion and deployment
orchestration records give operators a cloud-neutral proof trail for builds, registry
promotion, Helm render intent, and discovery reloads. Remaining follow-up work is
tracked in [Production Roadmap Closeout](docs/ROADMAP_CLOSEOUT.md) and limited to repo
cleanup, container-first worker contract phases, per-goblin resource policies, public
packaging, and explicitly deferred cloud-specific recipes.
