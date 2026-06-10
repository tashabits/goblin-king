# Using Goblin King From Another Project

This guide is for host projects that want to define their own goblins and let Goblin
King schedule, run, and prove them.

## Install For Development

Use an editable install while building the integration:

```bash
python -m pip install -e ../goblin-king[dev]
```

If a host project chooses to use Python package metadata or tests, import only from the
root package boundary:

```python
from goblin_king import GoblinContext, GoblinDefinition, GoblinResult
```

Those Python imports are optional helpers for definitions, tests, and local debugging.
The deployed goblin worker is still a contract-compliant container. A host project can
write the worker itself in any language that follows the
[Goblin Container Contract](goblin-container-contract.md).

## Install As An Internal Wheel

Build and install a private wheel when the host project is ready to pin a version:

```bash
python -m pip wheel ../goblin-king -w dist
python -m pip install dist/goblin_king-*.whl
```

Pin the wheel version together with the API, scheduler, and admin Docker image tags used
by the deployment.

## Define Container Goblins In Project Config

For most projects, start with a standalone project config instead of a Python plugin:

```bash
goblin-king project init ./my-goblin-project --prefix myproject
cd ./my-goblin-project
```

The generated `goblin-king-project.json` defines project-owned container goblins and
points each kind at a self-contained worker folder with its own Dockerfile. See
[Adopter Guide](adopter-guide.md) for the full validation, run, schedule, and admin
proof path.

## Optional Python Package Metadata

Create a starter package only when the host project wants Python entry-point discovery
or Python-side metadata helpers:

```bash
goblin-king project init-package ./my-goblins \
  --kind project.hello \
  --package-name project_goblins \
  --image project-hello:local
```

The generated package includes:

- Python package metadata.
- A `goblin_king.goblins` entry point.
- `goblins.json` for JSON-based integration.
- `goblin-images.json`.
- `goblin-king-project.json` using entry-point discovery by default.
- `goblin-king-api.json`.
- A self-contained worker folder with a Dockerfile.
- A long-running service worker folder with a Dockerfile.
- Local tests and README.

The generated Python worker is one implementation path, not a requirement. Projects may
replace the worker folder with Go, Rust, Node.js, shell, container-wrapped WASI, or any
other OCI image that obeys the same contract.

## Connect The Host Project

Use `goblin-king-project.json` to describe the host project's registries, optional
entry points, worker image map, inline container goblins, and API settings:

```json
{
  "registries": ["goblins.json"],
  "entry_points": true,
  "images": "goblin-images.json",
  "api_settings": "goblin-king-api.json"
}
```

Validate discovery before deployment:

```bash
goblin-king project validate --project goblin-king-project.json
goblin-king project goblins list --project goblin-king-project.json
```

Use `--check-worker-builds` when deployment proof needs real Docker builds as part of
validation:

```bash
goblin-king project validate --project goblin-king-project.json --check-worker-builds
```

See `examples/adopting-project/` for a multi-registry host-project fixture.

## Use Docker Images

Host projects can use Goblin King through its service images while keeping goblin worker
images project-owned:

- API image runs `goblin-king api run`.
- Scheduler image runs `goblin-king scheduler run`.
- Admin image serves the React tester interface.
- Project worker images implement the Goblin King worker contract.

Build worker images before running the scheduler:

```bash
goblin-king workers build --images goblin-images.json
```

Then start API, scheduler, Redis, and admin through Docker Compose or Helm.

For a concrete Compose extension, see
`examples/adopting-project/docker-compose.host-project.yml`. From the repository root:

```bash
docker compose \
  -f docker-compose.yml \
  -f examples/adopting-project/docker-compose.host-project.yml \
  --profile api \
  --profile admin \
  --profile project-workers \
  up -d --build redis api admin long-hello worker-project-maintenance-hello
```

The extension mounts the project fixture at `/project`, runs the API with
`/project/goblin-king-api.json`, and runs the scheduler with
`--project /project/goblin-king-project.json`.

## Use Helm

Host projects can supply extra values rather than forking the chart:

```bash
helm template goblin-king charts/goblin-king -f examples/adopting-project/helm-values.yaml
```

The pattern is:

- Mount project configuration with `project.extraVolumes` and
  `project.extraVolumeMounts`.
- Set `config.projectSettingsPath` so API and scheduler both load the host project.
- Keep `config.registryPath` and `config.imagesPath` as fallback/direct paths.
- Add long-running service workers with `workers.extraLongServices`.
- Reload discovery after deploy or upgrade so the admin goblin list updates at runtime.

## Reload Discovery After Deploy

After installing a new plugin wheel, mounting a changed registry, or updating
`goblin-images.json`, reload discovery before submitting the new goblin kind:

```bash
curl -X POST http://127.0.0.1:8000/admin/discovery/reload \
  -H "Authorization: Bearer local-dev-token"
curl -H "Authorization: Bearer local-dev-token" http://127.0.0.1:8000/admin/discovery/sources
```

The React admin exposes the same flow in **Discovery**. It reads goblins dynamically
from the API, so project goblin types appear after reload without a React rebuild. If a
reload fails because of duplicate kinds, invalid registry files, or missing image maps,
the previous valid registry remains active and the error is displayed for operators.

Makefile shortcuts for the local fixture:

```bash
make project-validate
make project-build-workers
make project-discovery-reload
make project-admin-proof
```

## Proof Checklist

For a host project to be "off to the races":

- Project goblins appear in `goblin-king project goblins list`.
- Worker image map covers every Docker-backed goblin.
- Worker images build locally.
- API/admin `GET /goblins` shows project goblins after discovery reload.
- A short job completes through the scheduler.
- Events, runs, heartbeats, and artifacts are visible in the admin guide paths.
