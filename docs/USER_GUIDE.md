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

## Understand The Worker Contract

Goblin King runs goblins as contract-compliant OCI/Docker containers. It schedules
containers, not Python functions or language-specific runtimes. The worker code inside
the container can use any language as long as it follows the
[Goblin Container Contract](goblin-container-contract.md).

Python helpers and in-process runtime support are useful for local debugging and package
definitions, but they are optional conveniences. Production worker execution should be
designed around the container contract.

Worker authors should read:

- [Goblin Workload Types](goblin-workload-types.md)
- [What Is A Goblin?](what-is-a-goblin.md)
- [Writing Goblins](writing-goblins.md)
- [Goblin Dockerfiles](goblin-dockerfiles.md)
- [Language-Agnostic Workers](language-agnostic-workers.md)
- [Security Model](security-model.md)

## Run A Short Job

Queue and execute the short Hello World sample:

```bash
goblin-king jobs submit example.hello \
  --input examples/input.json \
  --registry demo-goblins.json \
  --images demo-images.json \
  --redis-url redis://localhost:6379/0
```

The result contains `Hello World` in the structured `GoblinResult` envelope.

## Run The Scheduler

Create a due schedule and run one deterministic scheduler pass:

```bash
goblin-king schedules add example.echo \
  --cron "* * * * *" \
  --input examples/input.json \
  --registry demo-goblins.json \
  --due-now

goblin-king scheduler run-once \
  --registry demo-goblins.json \
  --images demo-images.json \
  --redis-url redis://localhost:6379/0
```

Inspect persisted state:

```bash
goblin-king jobs list
goblin-king events list --limit 20
goblin-king events stream-status
goblin-king events stream-read --ack
goblin-king heartbeats list
```

The event `sequence` is the authoritative lifecycle order; `created_at` remains UTC
correlation metadata. This distinction keeps fast-task evidence causal when a host
clock adjusts. See [Causal Lifecycle Ordering](causal-lifecycle-ordering.md).

Use the Redis Stream or durable event API when ordered replay matters. Pub/sub is a low-latency,
best-effort notification rail; consumers spanning multiple publishers order and deduplicate those
notifications by the included sequence.

## Stream Live Run Progress

Fixed task workers can optionally publish bounded `progress`, `stdout`, `stderr`, and
`message` events before the container exits. Python workers that install Goblin King can
use the runtime-provided configuration directly:

```python
from goblin_king import RunEventPublisher

events = RunEventPublisher.from_environment()
events.stdout("loading input\n")
events.progress(25, "input loaded")
```

Replay retained events for an authorized Run, then reconnect from the last sequence:

```bash
curl -H "Authorization: Bearer local-dev-token" \
  "http://127.0.0.1:8000/runs/<run-id>/events?after_sequence=0&limit=100"
```

Live clients connect to
`WS /ws/runs/<run-id>/events?token=<project-token>&after_sequence=<last-sequence>`.
This per-run stream is separate from the durable lifecycle event history returned by
`GET /events` and broadcast through `WS /ws/runs`. Its replay window is bounded, so
durable output still belongs in the final result or retained artifacts. See
[Live Run Events](live-run-events.md) for non-Python publication, limits, reconnect
behavior, and the Docker/Kubernetes contract.

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
It lists goblins, worker images, jobs, schedules, runs, fanouts, service goblins,
events, heartbeats, artifacts, audit logs, and rate-limit proof panels. The lab bench
captures request payloads, responses, durable events, Redis Stream delivery health, and
live WebSocket messages. King-side kill controls cancel jobs or stop registered
services. Admin hard-kill runtime controls are separate and only target Docker
containers or Kubernetes Jobs with Goblin King ownership labels.

The default demo setup uses `demo-goblins.json` and `demo-images.json`, so the Goblin
Lab dropdown includes the core samples plus explicit language goblins for .NET, Go,
Java, Node.js, PHP, Python, Ruby, Rust, shell, C/WASI, and Rust/WASI.

For a screenshot walkthrough of each admin panel, see
[Goblin King Admin Guide](ADMIN_GUIDE.md).

Use the Admin/Auth cleanup controls to remove old runtime rows after a testing pass.
Always preview first; removal clears terminal jobs and runs, completed fanouts, captured
events, worker heartbeats, and stopped or unprobed service rows. It preserves schedules,
users, projects, API tokens, active jobs, running services, and scheduler heartbeat.

Use the Runs & Artifacts artifact-volume controls to inspect filesystem-backed storage.
The API reports the configured root, whether it exists and is writable, file count,
total bytes, and artifact metadata count. Cleanup is admin-only and always supports a
dry run first:

```bash
curl -H "Authorization: Bearer local-dev-token" \
  http://127.0.0.1:8000/admin/artifacts/storage
curl -X POST http://127.0.0.1:8000/admin/artifacts/cleanup \
  -H "Authorization: Bearer local-dev-token" \
  -H "Content-Type: application/json" \
  -d "{\"dry_run\":true,\"max_total_bytes\":0}"
```

Docker Compose stores artifact bytes in the `goblin-king-data` volume under
`.goblin-king/artifacts`; Helm stores them on the chart PVC at `/data/artifacts`.
For Kubernetes tasks, the worker writes only to a transient `/artifacts` `emptyDir`.
The trusted result forwarder validates and atomically copies declared files into the
PVC before publishing the final result and before Job deletion. Failed or incomplete
retention yields a failed result with no artifact entries, so metadata never claims
missing bytes. See [Kubernetes Artifact Retention](kubernetes-artifact-retention.md).

Every artifact returned from `GET /runs/<run-id>/artifacts` includes an authenticated
`download_url`. Download an artifact by its declared name:

```bash
curl -H "Authorization: Bearer local-dev-token" \
  -o report.txt \
  http://127.0.0.1:8000/runs/<run-id>/artifacts/report.txt
```

Docker workers may return the documented `artifact://report.txt` locator. The API
resolves it only inside that job's mounted artifact directory and only when the locator
matches the declared artifact name. Relative paths, supported local `file://` locators,
project authorization, and root-containment checks retain their existing behavior.

For a worker that runs on both Docker and Kubernetes, prefer a relative path or a local
`file:` URI beneath `GOBLIN_ARTIFACT_ROOT`. Kubernetes durable retention intentionally
rejects non-file schemes. Derive file URIs with the language runtime rather than string
concatenation; Node workers should use `pathToFileURL(artifactPath).href` from `node:url`.
The `example.behavior-node-artifact` worker demonstrates this portable form.

## Hard-Kill Runtime Objects

Use hard-kill controls only when a running worker runtime object needs to be stopped
outside the normal scheduler path. The API only targets runtime objects labeled by
Goblin King:

```bash
curl -X POST http://127.0.0.1:8000/admin/runtime/jobs/<job-id>/kill \
  -H "Authorization: Bearer local-dev-token" \
  -H "Content-Type: application/json" \
  -d "{\"runtime\":\"both\"}"
curl -X POST http://127.0.0.1:8000/admin/runtime/runs/<run-id>/kill \
  -H "Authorization: Bearer local-dev-token" \
  -H "Content-Type: application/json" \
  -d "{\"runtime\":\"docker\"}"
```

The response lists killed runtime objects and any runtime errors. A job hard-kill also
marks a non-terminal job as cancelled. Registered service goblins can be
hard-stopped from the admin UI or `/admin/runtime/services/{service_id}/kill`; this
changes King-side service presentation because those services are registered by URL.

## Promote Images And Record Deployment Proof

Phase 21 adds cloud-neutral proof records for image promotion and deployment
orchestration. These records are useful for local and internal release flows before a
project chooses registry-specific automation.

Plan an image promotion:

```bash
goblin-king deploy promotions plan example.hello \
  --target-image registry.example/goblin-king-example-hello:prod \
  --images demo-images.json --build --push
goblin-king deploy promotions list
```

After an external registry push or promotion is complete, mark the proof record:

```bash
goblin-king deploy promotions mark <promotion-id> \
  --status promoted \
  --digest sha256:<digest>
```

Record Helm render intent:

```bash
goblin-king deploy helm-template --chart charts/goblin-king --release goblin-king
goblin-king deploy records
```

The same paths are available through authenticated admin API endpoints under
`/admin/images/promotions` and `/admin/deployments`. The React admin exposes them in
**Image Promotion & Deploy**, including worker image coverage, promotion history, Helm
render records, and discovery reload proof.

The API requires bearer auth for everything except `/health`:

```bash
curl -H "Authorization: Bearer local-dev-token" http://127.0.0.1:8000/goblins
```

For external identity, enable OIDC/JWT validation in `goblin-king-api.json`. Local API
tokens are checked first, then OIDC validation runs when no local token matches:

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

The role claim maps configured admin roles to `admin`; otherwise callers become
project-scoped `member` or `viewer` principals according to their claims.

JupyterHub can be enabled as an optional auth provider for same-cluster service access.
Local API tokens still win first; when no local token matches, Goblin King can validate
a Hub user API token, map Hub groups to roles/projects, and authorize service proxy
requests. See [JupyterHub Service Access](jupyterhub-service-access.md) for the
zero-to-jupyterhub values file, Helm flag, and exact Hub service config.

## Prove The Long-Running Service

Start the sample long-running service goblin:

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

Authenticated callers can access registered HTTP services through Goblin King instead
of reaching the service URL directly:

```bash
curl -H "Authorization: Bearer local-dev-token" \
  http://127.0.0.1:8000/services/long-running/<service-id>/proxy/hello
```

The proxy only targets registered service base URLs, enforces project scope, audits the
request, and strips standard auth/cookie headers before forwarding.

### Connect to a ready service with WebSockets

Services that need a long-lived duplex channel use the same registered proxy path with
a WebSocket upgrade. Probe the service first, then connect with a bearer token or, for
the browser WebSocket constructor, a scoped query token:

```javascript
const socket = new WebSocket(
  "ws://127.0.0.1:8000/services/long-running/<service-id>/proxy/socket" +
    "?token=<scoped-token>",
  ["l2l.v1"],
);

socket.addEventListener("open", () => socket.send("hello"));
socket.addEventListener("message", event => console.log(event.data));
socket.addEventListener("close", event => console.log(event.code, event.reason));
```

Goblin King rejects unready services and callers outside the service project. It strips
the control-plane token and client credentials before the upstream handshake, preserves
text and binary messages, and applies operator-configured message, queue, write, idle,
open, close, and rolling-drain limits. Published repository service replacements become
active only after readiness succeeds; existing connections drain against the old
runtime while new connections use the promoted one.

Read [Managed-Service WebSocket Proxy](managed-service-websockets.md) for route forms,
configuration, close codes, security boundaries, rolling behavior, Compose/Kubernetes
proof commands, and troubleshooting.

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

For the included host-project fixture, run:

```bash
make project-validate
make project-build-workers
make project-discovery-reload
make project-admin-proof
```

The proof should show `project.maintenance.hello` and `project.reports.long-service`
coming from the mounted project settings. The admin Discovery panel screenshot in the
Admin Guide shows where the reload and source coverage appear in the UI.

## Optional Kubernetes Deployment

Render the chart locally:

```bash
make helm-template
```

The chart includes API/admin, scheduler, Redis, persistent storage, and the sample
service goblin. Admin ingress is on by default:

```bash
helm template goblin-king charts/goblin-king
```

Pin a registry deployment by digest so the scheduler control container and default
result-forwarder sidecar use the same immutable image:

```bash
helm template goblin-king charts/goblin-king \
  --set-string image.repository=registry.example/tashabits/goblin-king \
  --set-string image.digest=sha256:<digest> \
  --set image.pullSecrets[0].name=primary-registry
```

Use `scheduler.resultForwarder.image.*` only when the forwarder is published separately.
Use `scheduler.workloadImagePullSecrets` for additional existing Secret names needed by
generated worker Pods. Full value precedence, direct CLI options, API JSON, security
boundaries, and pull-failure behavior are documented in
[Kubernetes Runtime Images](kubernetes-runtime-images.md).

Existing adopters remain on the `legacy` workload-security profile. To migrate a chart
after confirming the images run non-root and read-only, render both settings together:

```bash
helm template goblin-king charts/goblin-king \
  --set-string scheduler.workloadSecurity.profile=restricted-v1 \
  --set resourcePolicies.defaults.filesystem.read_only_root=true
```

Do not omit the resource-policy change: an explicit read-write root conflicts with the
restricted profile and is intentionally rejected. See
[Kubernetes Workload Security](kubernetes-workload-security.md) for UID/GID, forwarder
resources, per-kind ServiceAccounts, validation identity, and the adoption checklist.

The default persistent artifact mapping is:

```yaml
persistence:
  enabled: true
  artifactSubdirectory: artifacts
config:
  artifactRoot: /data/artifacts
```

Keep the PVC subdirectory and API-visible root aligned. Artifact-producing Kubernetes
Jobs fail explicitly when persistent retention is not configured; artifact-free Jobs
remain compatible. For multi-node clusters, use storage that supports simultaneous
attachment by the API and worker Pods, normally `ReadWriteMany`, or provide equivalent
scheduling/storage guarantees.

Disable ingress when another deployment layer owns routing:

```bash
helm template goblin-king charts/goblin-king --set admin.ingress.enabled=false
```

For production-like Kubernetes installs, keep the same chart and set the neutral
hardening values your cluster expects:

```bash
helm template goblin-king charts/goblin-king \
  --set api.resources.requests.cpu=100m \
  --set api.resources.requests.memory=256Mi \
  --set api.autoscaling.enabled=true \
  --set scheduler.podDisruptionBudget.enabled=true \
  --set persistence.storageClassName=standard \
  --set api.existingSecret=goblin-king-secrets \
  --set networkPolicy.enabled=true
```

The chart supports resource limits, HPAs, PDBs, node selectors, tolerations, affinity,
image pull secrets, pod/container security contexts, PVC access modes, ingress TLS, and
externally managed Kubernetes Secrets. Cloud-specific managed ingress, external secret
controllers, and storage class choices are intentionally left to the deploying project.

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

Before the first generic registry job, create scheduler proof from inside the cluster:

```bash
kubectl port-forward service/goblin-king-api 8000:8000

curl -X POST http://127.0.0.1:8000/admin/workers/validate-kubernetes \
  -H "Authorization: Bearer local-dev-token" \
  -H "Content-Type: application/json" \
  -d '{"kinds":["example.hello"],"input":{"message":"cluster proof"},"require_success":true,"timeout_seconds":120}'
```

The response includes the exact `kubernetes:<configured-image>` gate identity, result
status, exit code when Kubernetes reports one, bounded per-container logs, and artifact
metadata. The API persists the pass/fail record in the chart database, so the scheduler
can accept the first normal job without a manual database seed or Docker validation.
Only admins can invoke this operation. Prefer digest-pinned worker references for
immutable deployment proof and treat returned logs as potentially sensitive worker
output. Follow the [exact generic worker proof](kubernetes-generic-worker-validation-proof.md)
for install, validation, scheduling, and cleanup checks.

Normal scheduled Kubernetes runs also persist a bounded `worker.container_logs` event
before their transient Job is deleted. Because Kubernetes returns one combined Pod log
stream, read its `stdout` field together with `stream_mode: combined`; `stderr` is empty.
The event contains only the user `worker` container output, not result-forwarder
infrastructure diagnostics. Use the truncation fields to tell whether the effective
`logs.max_bytes` policy clipped the retained tail. A false `byte_count_exact` means the
reported byte count is only a bounded lower bound, or that the Kubernetes log request
failed and the user output was intentionally left empty.

## Release And Upgrade

For project-ready adoption, use:

- [First Hour Guide](FIRST_HOUR.md)
- [Release Checklist](RELEASE_CHECKLIST.md)
- [Compatibility Matrix](COMPATIBILITY.md)
- [Upgrade Guide](UPGRADING.md)
- [Migration Guide](MIGRATION_GUIDE.md)

## Sample Goblins

- `example.hello`: short-running Hello World proof.
- `example.long-hello`: service goblin with timestamped probe responses.
- `example.artifact`: writes a small text artifact and returns artifact metadata.
- `example.environment`: reports safe runtime and context details.
- `example.controlled-failure`: returns a predictable failed result.
- `example.progress`: emits step-style progress data and handoff metadata.

## Pull Request Proof

All project quality gates are local. PRs should include exact `pytest` and `ruff`
output plus any Docker, API, admin UI, Helm, or kind smoke evidence that applies.
