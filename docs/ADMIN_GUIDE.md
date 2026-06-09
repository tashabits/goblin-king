# Goblin King Admin Guide

The React admin is the operator lab bench for proving goblins work. It runs as the
same UI in Docker Compose and Helm: Docker serves it at `http://127.0.0.1:8080/admin`,
and the local Helm chart serves it at `http://goblin-king.local/admin`.

## Sign In

Use an API bearer token to enter the admin. For local development, the default token is
`local-dev-token`.

![Admin login screen](images/admin/admin-login.png)

## Read The Dashboard

The dashboard gives a quick health read: active tasks, failed or cancelled tasks,
completed tasks, running long services, and the registered goblin list. Use **Refresh
all** after external CLI or scheduler actions.

![Admin dashboard](images/admin/admin-dashboard.png)

## Spawn A Goblin

Use **Goblin Lab** to submit one-shot jobs. Pick a goblin kind, edit the JSON input,
and press **Submit job**. The quick buttons run common proof paths:

- **Hello proof** submits the short `example.hello` worker.
- **Failure proof** submits the controlled failure sample.
- **Progress proof** submits the heartbeat/progress sample.

The captured traffic panel records the request and response so the proof is visible in
the UI.

![Goblin Lab job submission](images/admin/admin-goblin-lab.png)

## Watch And Cancel Tasks

The **Task Board** shows queued, running, retrying, completed, failed, timed-out, and
cancelled work. The **Kill / cancel** control is King-side cancellation: it cancels
queued or cancellable jobs through the API. It does not hard-kill Docker containers or
Kubernetes pods.

![Task Board](images/admin/admin-task-board.png)

## Probe Long Services

Use **Services** for long-running goblins. Register the deployment URL, then press
**Probe** to call the service through the King. The `example.long-hello` service returns
`Hello World from long running service` with a fresh timestamp on every probe.

Use **Stop service** to mark a registered long-running service as stopped. Like job
cancel, this is King-side state control rather than hard runtime termination.

![Long-running service controls](images/admin/admin-services.png)

## Inspect Events And Heartbeats

The **Events** panel shows durable SQLite-backed event history and the live WebSocket
event rail. Heartbeats appear below this section and prove scheduler and worker liveness.
If a goblin moves, the King writes it down.

![Events and live rail](images/admin/admin-events.png)

## Clean Up Old Rows

Use **Admin & Auth -> Cleanup** after a test pass. Always press **Preview old rows**
first. The preview shows exactly what will be removed.

Cleanup can remove:

- Terminal jobs and runs.
- Completed fanouts.
- Captured events.
- Worker heartbeats.
- Stopped long-service rows.
- Optional unprobed registered long-service rows.

Cleanup preserves:

- Schedules.
- Users, projects, API tokens, and auth data.
- Active jobs.
- Running services.
- Scheduler heartbeat.

Press **Remove previewed rows** only after the counts look right.

![Cleanup controls](images/admin/admin-cleanup.png)

## Docker And Helm Notes

Docker Compose and Helm use the same React build and the same API paths:

- UI: `/admin`
- API proxy: `/admin-api/*`
- WebSocket proxy: `/admin-ws/runs`

For local Helm, make sure `goblin-king.local` points to the local ingress controller.
The README has the current Windows hosts-file note and ingress defaults.
