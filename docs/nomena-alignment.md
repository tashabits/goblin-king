# Nomena Alignment Notes

Goblin King keeps the model that worked well in Nomena-style maintenance tooling:

- A control API coordinates work and exposes operator proof flows.
- Goblin King supervises queued jobs, schedules, events, and heartbeats.
- Workers execute in isolated containers with explicit input and result contracts.
- Redis carries transient result and event messages.
- SQLite remains the durable local source of truth.
- Operator UI flows can spawn proof jobs, inspect events, and verify liveness.

## Mapping Existing Concepts

Nomena-style queue workers can map their task names to `GoblinDefinition.kind` values.
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
