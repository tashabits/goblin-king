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

## Phase 7

- `GET /events`: durable event stream for jobs, runs, scheduler activity, and workers.
- `WS /ws/runs`: live run notifications over WebSockets.
- Redis pub/sub status streaming.
- Scheduler and worker heartbeat APIs.

## Phase 8

- Production authentication and authorization.
- Users, teams, projects, API tokens, audit logs, and rate limits.
- Pagination/filtering hardening for large job, run, and schedule sets.
- OpenAPI customization for generated clients.

## Later Infrastructure Phases

- Kubernetes runtime APIs.
- Deployment and build orchestration APIs.
- Worker image registry promotion APIs.
- Artifact storage provider APIs.
- External webhook callbacks.
