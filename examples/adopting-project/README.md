# Adopting Project Example

This fixture shows how a host project can describe multiple goblin packages without
copying Goblin King internals.

It demonstrates:

- Multiple registry files in one project settings file.
- Inline `GoblinProject` config for `project.inline.hello`.
- Worker image map coverage for each discovered goblin.
- A short-running goblin and a long-running service goblin.
- Static validation before Docker/Helm deployment.
- Docker Compose extension settings for project workers.
- Helm values for mounting project config and deploying an extra long-running service.

Validate the fixture from the repository root:

```bash
goblin-king project validate --project examples/adopting-project/goblin-king-project.json
goblin-king project goblins list --project examples/adopting-project/goblin-king-project.json
```

Build the worker images when Docker proof is needed:

```bash
goblin-king workers build --images examples/adopting-project/goblin-images.json
```

## Docker Compose Extension

From the repository root, layer this fixture over the base Compose stack:

```bash
HOST_PROJECT_PATH=./examples/adopting-project \
docker compose \
  -f docker-compose.yml \
  -f examples/adopting-project/docker-compose.host-project.yml \
  --profile api \
  --profile admin \
  --profile project-workers \
  up -d --build redis api admin long-hello worker-project-maintenance-hello
```

Then reload discovery and confirm the project goblin appears:

```bash
make project-discovery-reload
make project-admin-proof
```

The admin should list `project.inline.hello`, `project.maintenance.hello`, and
`project.reports.long-service` without rebuilding the React UI.

## Helm Values

Render the chart with host-project values:

```bash
helm template goblin-king charts/goblin-king -f examples/adopting-project/helm-values.yaml
```

The values file assumes a `project-goblin-config` ConfigMap supplies
`goblin-king-project.json`, `goblin-images.json`, and the `registries/` files under
`/project`. The chart mounts that config into API and scheduler pods, passes
`--project /project/goblin-king-project.json` to the scheduler, and creates an extra
long-running service for `project.reports.long-service`.
