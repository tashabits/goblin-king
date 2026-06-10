# Project Adoption Notes

Goblin King keeps a project-friendly maintenance tooling model:

- A control API coordinates work and exposes operator proof flows.
- Goblin King supervises queued jobs, schedules, events, and heartbeats.
- Workers execute in isolated containers with explicit input and result contracts.
- Redis carries transient result and event messages.
- SQLite remains the durable local source of truth.
- Operator UI flows can spawn proof jobs, inspect events, and verify liveness.

## Mapping Existing Concepts

Existing queue workers can map their task names to `GoblinDefinition.kind` values.
Existing worker images can become self-contained worker folders with a `Dockerfile` and
the Goblin King worker contract. Queue payloads become job `input` JSON. Existing result
keys and fanout proofs become durable jobs, fanouts, runs, events, and heartbeats.

Long-running maintenance services should be modeled as service goblins. The King
registers their service URL, probes them through the API/admin UI, captures request and
response payloads, and records events that prove the service is alive.

## Adoption Shape

Projects can start with Docker Compose, then opt into the Helm chart when Kubernetes is
required. The same registry, image map, API, scheduler, event, heartbeat, and admin UI
concepts apply in both deployment modes.

The project-ready adoption roadmap keeps existing projects in a container-first model:
Goblin King is installed as an internal wheel or vendored dependency, while the host
project owns project goblin packages, registry files, worker folders, and image maps.
The admin/API should discover those project goblins at deploy time and support runtime
reload, so newly deployed goblin types appear in the admin without rebuilding the React
UI.

For existing maintenance work, the migration path should be:

1. Turn each task name into a stable `GoblinDefinition.kind`.
2. Move queue payloads into typed job input JSON.
3. Move existing worker code into self-contained worker folders or project goblin packages.
4. Emit `GoblinResult` envelopes with artifacts, metrics, handoffs, and failure details.
5. Use events and heartbeats as the operator proof trail.
6. Reload discovery after deployment and prove the new goblins from the admin lab bench.

The `examples/adopting-project/` fixture shows this shape with multiple registry files,
worker image coverage, and a short plus long-running project goblin pair.
