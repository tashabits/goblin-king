# Adopter Admin Dev/Test Stack

This guide shows how an adopting project can run the Goblin King control plane and React
admin locally, then use the admin to inspect project-defined goblins.

The boring loop is:

```text
start stack -> open admin -> reload discovery -> validate goblins -> submit job -> inspect run/result/artifacts
```

## Primary path: Docker Compose

For the included host-project fixture, run this from the Goblin King repository root:

```bash
HOST_PROJECT_PATH=./examples/adopting-project \
docker compose \
  -f docker-compose.yml \
  -f examples/adopting-project/docker-compose.host-project.yml \
  --profile api \
  --profile admin \
  --profile scheduler \
  --profile project-workers \
  up -d --build redis api admin scheduler long-hello worker-project-maintenance-hello
```

For a host project that vendors Goblin King under `vendor/goblin-king`, run the same
shape from the host project root:

```bash
HOST_PROJECT_PATH=. docker compose \
  -f vendor/goblin-king/docker-compose.yml \
  -f vendor/goblin-king/examples/adopting-project/docker-compose.host-project.yml \
  --profile api \
  --profile admin \
  --profile scheduler \
  --profile project-workers \
  up -d --build redis api admin scheduler long-hello worker-project-maintenance-hello
```

Open `http://127.0.0.1:8080/admin` and sign in with the configured local token. The
development default is `local-dev-token`.

## Validate project config and workers

Before scheduling a project goblin, validate config and worker image behavior:

```bash
goblin-king project validate --project examples/adopting-project/goblin-king-project.json
goblin-king workers validate \
  --project examples/adopting-project/goblin-king-project.json \
  --input examples/input.json \
  --kind project.inline.hello \
  --build \
  --require-success
goblin-king workers validation-status --kind project.inline.hello
```

In a real host project, replace `examples/adopting-project/goblin-king-project.json`
with the project-local `goblin-king-project.json` path and use a project input file.

## Reload discovery and inspect admin state

After the stack starts, reload discovery so the API/admin sees project goblins:

```bash
make project-discovery-reload
make project-admin-proof
```

The admin should show:

- project goblin kinds,
- source or project marker such as `project-config`,
- validation status,
- worker image mapping,
- effective resource policy after runs,
- jobs, runs, result JSON, events, and artifacts.

## Submit a project goblin and inspect the run

Submit a project goblin through Docker:

```bash
goblin-king jobs submit project.inline.hello \
  --project examples/adopting-project/goblin-king-project.json \
  --input examples/input.json \
  --runtime docker
```

Inspect the returned run:

```bash
goblin-king runs show <run-id> --with-job
```

Refresh the admin **Task Board** and **Runs & Artifacts** panels. The job/run/result
should appear there too. If the goblin produces artifacts, artifact metadata and links
appear in run detail and the admin artifact views.

## Secondary path: local API plus admin dev server

Use this when actively developing the React admin. Start Redis, run the API, then start
the Vite dev server:

```bash
docker compose up -d redis
goblin-king api run --settings goblin-king-api.json
cd admin-ui
npm install
npm run dev
```

This mode is for UI development. The Compose admin image is the repeatable proof path for
adopter stack testing.

## Docker socket security

In local Docker mode, the Goblin King scheduler/control plane may need Docker socket
access to launch goblin containers. The Docker socket is security-sensitive.

Rules:

- The control plane may receive Docker socket access in trusted local/dev mode.
- Goblin task containers must not receive Docker socket access.
- Do not mount host-sensitive paths into goblin task containers.
- Do not expose the local admin/API stack publicly without proper auth and TLS.
- Do not use this workflow for untrusted arbitrary containers.

## Shut down

Stop the host-project stack:

```bash
docker compose \
  -f docker-compose.yml \
  -f examples/adopting-project/docker-compose.host-project.yml \
  --profile api \
  --profile admin \
  --profile scheduler \
  --profile project-workers \
  down
```

Use `--volumes --remove-orphans` when you intentionally want a clean local reset.

## Helm proof

For Kubernetes-oriented proof, render the host-project values:

```bash
helm template goblin-king charts/goblin-king -f examples/adopting-project/helm-values.yaml
```

Helm remains optional for adopter development. Docker Compose is the primary local path.
