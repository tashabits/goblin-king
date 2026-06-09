# API Roadmap

Phase 4 implements the local control-plane API for goblins, queued jobs, schedules,
runs, and safe artifact access. APIs not listed in Phase 4 stay out of scope until the
target phase below.

## Covered In Phase 5

- Registry discovery for package entry points and reusable project integration.
- Package/project integration commands for adopting Goblin King from existing codebases.

## Covered In Phase 6

- `POST /jobs/fanout`: submit many related jobs and collect matching run IDs.
- `POST /jobs/{job_id}/retry`: create a retry attempt from a failed or timed-out job.
- `GET /fanouts`: list durable fanout batches with derived status.
- `GET /fanouts/{fanout_id}`: inspect one fanout batch, child jobs, and runs.

## Covered In Phase 7

- `GET /events`: durable event stream for jobs, runs, scheduler activity, and workers.
- `WS /ws/runs`: live run notifications over WebSockets.
- Redis pub/sub status streaming.
- Scheduler and worker heartbeat APIs.

## Covered In Phase 8

- Production authentication and authorization.
- Users, teams, projects, API tokens, audit logs, and rate limits.
- Pagination/filtering hardening for large job, run, and schedule sets.
- OpenAPI customization for generated clients.

## Covered In Final Optional Phase

- FastAPI-served admin UI for Docker and Helm deployments.
- Long-running service registration and probe API for `example.long-hello`.
- Admin proof events for service registration, probes, responses, and failures.
- Optional Helm chart with API/admin, scheduler, Redis, persistence, sample worker
  service, and default-on configurable ingress.

## Covered In Phase 13

- `POST /admin/discovery/reload`: reload project settings, registry files, entry points,
  and worker image maps without rebuilding the React admin.
- `GET /admin/discovery/status`: return active goblin count, image map coverage, current
  discovery version, last successful reload, last failed reload, and validation status.
- `GET /admin/discovery/sources`: list loaded sources, entry-point goblins, rejected
  definitions, duplicate kind errors, and worker image map coverage.
- Scheduler discovery refresh through the same discovery version marker.
- Admin Discovery panel that calls these endpoints and displays reload proof.

## Covered In Phase 14

- Host-project deployment integration for extra registries, worker image maps, plugin
  wheels, and long-running services in Docker Compose and Helm.
- Post-deploy or post-upgrade discovery reload proof for newly deployed project goblins.

## Covered In Phase 15

- Internal release and upgrade proof APIs or commands as needed for package compatibility
  checks, sample adopting-project smoke tests, and version matrix reporting.

## Covered In Phase 16

- Production Kubernetes hardening in the optional Helm chart: resources, autoscaling,
  disruption budgets, pod placement, security contexts, image pull secrets, service
  accounts/RBAC, NetworkPolicy, ingress TLS/options, PVC access modes, and
  externally managed bootstrap secrets.

## Covered In Phase 17

- Redis Streams event delivery alongside SQLite event history and Redis pub/sub.
- `GET /events/stream/status`: inspect stream length, generated IDs, consumer groups,
  and pending delivery counts.
- CLI stream inspection and consumer-group read/ack commands for local proof.

## Covered In Phase 18

- OIDC/JWT bearer authentication with issuer, audience, JWKS, and clock-skew validation.
- Claim mapping from external identity tokens into local roles and project scope.
- Local API tokens remain supported and take precedence when a token hash matches.

## Covered In Phase 19

- Volume/PVC-backed artifact storage management without adding object storage providers.
- `GET /admin/artifacts/storage`: inspect configured artifact root health, file counts,
  byte totals, and metadata counts.
- `POST /admin/artifacts/cleanup`: dry-run or execute project-scoped artifact cleanup
  from the configured filesystem root.
- Admin UI artifact-volume status and cleanup proof controls.

## Covered In Phase 20

- Scoped hard runtime termination for Goblin King-labeled Docker containers and
  Kubernetes Jobs.
- `POST /admin/runtime/jobs/{job_id}/kill`: terminate runtime objects for one job and
  cancel non-terminal job state.
- `POST /admin/runtime/runs/{run_id}/kill`: terminate runtime objects for one run.
- `POST /admin/runtime/services/{service_id}/kill`: hard-stop registered long-running
  service presentation.

## Covered In Phase 21

- Image promotion and deployment orchestration proof records.
- `GET /admin/images/promotions`: list worker image promotion history.
- `POST /admin/images/promotions`: plan worker image promotion with build/push command
  proof.
- `POST /admin/images/promotions/{promotion_id}/mark`: mark promotions as built,
  pushed, promoted, or failed.
- `GET /admin/deployments`: list Helm render and discovery reload proof records.
- `POST /admin/deployments/helm-template`: record or execute Helm template proof.
- `POST /admin/deployments/reload-discovery`: reload discovery and record deploy proof.
- Admin UI deployment panel for worker image coverage, promotion status, Helm render
  intent, discovery reload, and proof trail.

## Covered In Phase 22

- Production roadmap closeout audit in `docs/ROADMAP_CLOSEOUT.md`.
- Documentation and screenshot coverage for Phase 16-21 production proof surfaces.
- Explicit deferred item list for future phases.

## Later Infrastructure Follow-Up

- External webhook callbacks.
- Cloud-specific Kubernetes recipes, such as managed ingress controllers, external
  secret operators, cloud storage classes, and registry-specific image promotion.
- External identity providers beyond generic OIDC/JWT.
- Object storage providers beyond Docker volumes and Kubernetes PVCs.
