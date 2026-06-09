# Using Goblin King From Another Project

This guide is for host projects that want to define their own goblins and let Goblin
King schedule, run, and prove them.

## Install For Development

Use an editable install while building the integration:

```bash
python -m pip install -e ../goblin-king[dev]
```

Host-project goblin packages should import only from the root package boundary:

```python
from goblin_king import GoblinContext, GoblinDefinition, GoblinResult
```

## Install As An Internal Wheel

Build and install a private wheel when the host project is ready to pin a version:

```bash
python -m pip wheel ../goblin-king -w dist
python -m pip install dist/goblin_king-*.whl
```

Pin the wheel version together with the API, scheduler, and admin Docker image tags used
by the deployment.

## Generate A Goblin Package

Create a starter plugin package:

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

## Connect The Host Project

Use `goblin-king-project.json` to describe the host project's registries, entry points,
worker image map, and API settings:

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

## Proof Checklist

For a host project to be "off to the races":

- Project goblins appear in `goblin-king project goblins list`.
- Worker image map covers every Docker-backed goblin.
- Worker images build locally.
- API/admin `GET /goblins` shows project goblins.
- A short job completes through the scheduler.
- Events, runs, heartbeats, and artifacts are visible in the admin guide paths.
