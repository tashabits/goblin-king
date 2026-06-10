# Using Goblin King From A Vendored Checkout

This guide is for projects that want to keep Goblin King inside their own repository
while still using the normal Goblin King CLI, project config, Docker runtime, and admin
stack.

Vendoring includes the Goblin King source tree: Python package, CLI, docs, Docker
Compose files, Helm chart, admin UI source, examples, and tests. It does not change the
worker model. Project goblins still run as validated OCI/Docker containers, and adopter
projects should define goblins through `goblin-king-project.json` rather than importing
Goblin King internals.

## Mode 1: Install from a vendored checkout

Use a Git submodule when the host project wants a pinned external checkout:

```bash
git submodule add https://github.com/tashabits/goblin-king.git vendor/goblin-king
python -m pip install -e ./vendor/goblin-king
goblin-king --help
```

From the host project, keep project-owned goblin config at the host project root:

```bash
goblin-king project validate --project goblin-king-project.json
goblin-king project goblins list --project goblin-king-project.json
```

## Mode 2: Use a git subtree

Use a subtree when the host project wants Goblin King source committed directly into the
host repository:

```bash
git subtree add --prefix vendor/goblin-king https://github.com/tashabits/goblin-king.git main --squash
python -m pip install -e ./vendor/goblin-king
goblin-king --help
```

Update the subtree using the host project's normal Git workflow. Goblin definitions still
belong in the host project's `goblin-king-project.json`; avoid editing vendored Goblin
King internals to add project goblins.

## Mode 3: Use a local path dependency

For local development, a path dependency can point at the vendored checkout:

```text
-e ./vendor/goblin-king
```

The same path can be used in a development requirements file, a local virtual
environment, or a host-project Docker image build that installs Goblin King from source.

## Admin UI and built assets

The admin UI source is included when the repo is vendored under `admin-ui/`. The
repeatable local path is to use the Goblin King Docker Compose stack, which builds the
admin image and serves the React app at `/admin`.

Installing the Python package alone does not guarantee that prebuilt admin assets are
available to a host application. If the host project wants the admin UI, use the Docker
admin image, run the admin dev server, or add an explicit admin build step.

## Local Docker stack from a vendored checkout

From the host project root, run Compose with the vendored file path and mount the host
project as the project source:

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

Open `http://127.0.0.1:8080/admin`, log in with the configured local token, reload
Discovery, and confirm the project goblins appear.

For the full local admin workflow, see
[Adopter Admin Dev/Test Stack](adopter-admin-dev-stack.md). For a shorter admin
proof checklist, see
[Testing Your Project With The Admin Panel](testing-your-project-with-the-admin-panel.md).

## Validate and run project goblins

Use the same commands whether Goblin King is installed from a wheel, submodule, subtree,
or local path:

```bash
goblin-king project validate --project goblin-king-project.json
goblin-king workers validate \
  --project goblin-king-project.json \
  --input inputs/hello.json \
  --kind myproject.hello \
  --build \
  --require-success
goblin-king workers validation-status --kind myproject.hello
goblin-king jobs submit myproject.hello \
  --project goblin-king-project.json \
  --input inputs/hello.json \
  --runtime docker
goblin-king runs show <run-id> --with-job
```

Goblin task containers still launch as Docker containers. Container-wrapped WASI workers
are also Docker containers from Goblin King's point of view; the container entrypoint may
run Wasmtime or Wasmer internally.

## Docker socket warning

In local Docker mode, the Goblin King control plane may need Docker socket access so it
can launch goblin task containers. The Docker socket is security-sensitive. Do not mount
it into goblin task containers, do not use this workflow for untrusted arbitrary images,
and do not expose the local admin/API stack publicly without proper auth and TLS.

## Next links

- [Using Goblin King As Your Project Scheduler](using-goblin-king-as-a-project-scheduler.md)
- [Adopter Guide](adopter-guide.md)
- [Project Template Quickstart](project-template-quickstart.md)
- [Testing Your Project With The Admin Panel](testing-your-project-with-the-admin-panel.md)
- [Project Goblin Config](project-goblin-config.md)
- [Security Model](security-model.md)
