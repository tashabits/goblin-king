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

## Planned For Phase 14

- Host-project deployment integration for extra registries, worker image maps, plugin
  wheels, and long-running services in Docker Compose and Helm.
- Post-deploy or post-upgrade discovery reload proof for newly deployed project goblins.

## Planned For Phase 15

- Internal release and upgrade proof APIs or commands as needed for package compatibility
  checks, sample adopting-project smoke tests, and version matrix reporting.

## Later Infrastructure Follow-Up

- Kubernetes runtime APIs.
- Deployment and build orchestration APIs.
- Worker image registry promotion APIs.
- Artifact storage provider APIs.
- External webhook callbacks.
- Production Kubernetes hardening beyond the optional chart, such as managed ingress,
  external secret stores, autoscaling, cloud storage classes, and image promotion.
