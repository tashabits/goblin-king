# Migrating Scripts And Workers Into Project Goblins

Use this guide when a project already has scripts, queue workers, or maintenance jobs
and wants to move them behind Goblin King.

## 1. Name The Goblin Kind

Give each existing task a stable lowercase kind:

```text
billing.reconcile
reports.daily-summary
maintenance.backup
```

The kind becomes the API/admin/scheduler identity for that worker.

## 2. Define Input And Result

Write down:

- Required input fields.
- Optional input fields and defaults.
- Result `data` shape.
- Artifacts produced.
- Metrics emitted.
- Handoffs to other systems.
- Failure modes.

## 3. Wrap Execution

Short tasks become one-shot workers that read input/context JSON and return a
`GoblinResult` envelope. Long-running tasks become service workers with a probe endpoint
and heartbeat behavior.

## 4. Add Discovery

Add a registry entry, entry point, and worker image map entry. Validate locally:

```bash
goblin-king project validate --project goblin-king-project.json
goblin-king project goblins list --project goblin-king-project.json
```

## 5. Deploy And Reload

Build the worker image, deploy the API/scheduler/admin stack, then reload discovery:

```bash
make project-build-workers
make project-discovery-reload
make project-admin-proof
```

The admin Discovery panel should show the new kind before operators submit work.
