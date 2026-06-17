# Testing Your Project With The Admin Panel

This quickstart is the hands-on path for proving an adopting project's goblins from the
React admin. It uses the included adopter fixture, but the same commands work from a
host project that vendors Goblin King or installs it as a local path dependency.

The goal is simple:

```text
start stack -> validate -> reload discovery -> submit job -> inspect run -> shut down
```

## Prerequisites

- Docker Desktop or another local Docker engine.
- Goblin King installed in the active Python environment, or commands run as
  `python -m goblin_king.cli ...`.
- The local development token, `local-dev-token`.
- A project config such as `examples/adopting-project/goblin-king-project.json`.

Project goblins are containers. They do not need Python worker imports, and goblin task
containers must not receive the Docker socket.

## Shortcut: Included Demo

From the Goblin King repository root, the included adopter fixture can be diagnosed and
proved through the admin in two commands:

```bash
goblin-king doctor
goblin-king demo up
```

`demo up` starts the trusted Docker Compose admin stack, validates
`project.inline.hello`, reloads discovery, submits a Docker-backed job, waits for the
scheduler run, and prints the admin URL. Stop the stack with:

```bash
goblin-king demo down
```

## Step 1: Start The Local Stack

From the Goblin King repository root, start the adopter stack:

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

On PowerShell, set the environment variable first:

```powershell
$env:HOST_PROJECT_PATH='./examples/adopting-project'
docker compose -f docker-compose.yml -f examples/adopting-project/docker-compose.host-project.yml --profile api --profile admin --profile scheduler --profile project-workers up -d --build redis api admin scheduler long-hello worker-project-maintenance-hello
```

Open `http://127.0.0.1:8080/admin` and log in with `local-dev-token`.

You should see the dashboard, the Goblin Lab, and the left navigation. If the goblin list
looks stale, use **Discovery -> Reload discovery** after the next step.

## Step 2: Validate Project Config

Validate the project file before proving any worker image:

```bash
goblin-king project validate --project examples/adopting-project/goblin-king-project.json
```

You should see the project goblin kinds, resource defaults, and worker image coverage.
Fix config errors before continuing. Goblin King does not schedule an invalid project
definition.

## Step 3: Validate Project Goblins

Validate the worker container contract for the project goblin image:

```bash
goblin-king workers validate \
  --project examples/adopting-project/goblin-king-project.json \
  --input examples/input.json \
  --kind project.inline.hello \
  --build \
  --require-success
```

Then check the cached proof:

```bash
goblin-king workers validation-status --kind project.inline.hello
```

Expected result: validation records a passing proof for the resolved image identity,
contract version, validator version, and timestamp. If the image digest changes, rerun
validation before scheduling again.

## Step 4: Reload Discovery

Refresh the API-visible goblin list:

```bash
make project-discovery-reload
```

Or use the admin:

1. Open **Discovery**.
2. Press **Reload discovery**.
3. Confirm `project.inline.hello` appears in the active goblin list.

In **Goblin Lab**, the registered goblin table should show:

- `project.inline.hello`.
- `project-config` as the source marker.
- the mapped worker image.
- validation status and repair guidance if proof is missing or stale.

## Step 5: Submit A Project Goblin From CLI

Submit the project goblin through Docker:

```bash
goblin-king jobs submit project.inline.hello \
  --project examples/adopting-project/goblin-king-project.json \
  --input examples/input.json \
  --runtime docker
```

Expected result: the command prints a completed job/run result or a clear validation
failure. Keep the run ID from the output.

## Step 6: Watch It In Admin

Return to `http://127.0.0.1:8080/admin` and press **Refresh all**.

Check these panels:

- **Task Board**: the job appears with its status.
- **Runs & Artifacts**: the run detail shows result JSON, source job metadata, and
  artifact metadata if any artifacts were produced.
- **Events**: job queued/running/completed or validation failure events are visible.
- **Discovery**: the project goblin remains visible after reload.

The admin is API-driven, so newly discovered project goblins do not require a React
rebuild.

## Step 7: Inspect The Run From CLI

Use the run ID printed by `jobs submit`:

```bash
goblin-king runs show <run-id> --with-job
```

Expected result: the output includes the run result, source job fields, effective
resource policy, and artifact metadata when present. There is no separate
`goblin-king artifacts list` command yet; use `runs show --with-job` or the admin
**Runs & Artifacts** panel.

## Step 8: Confirm It Used The Docker Runtime

The primary proof is the `jobs submit ... --runtime docker` command plus the completed
run record. For active jobs, long-running workers, or runtime modes that retain container
history, you can also inspect Docker labels while the stack is running:

```bash
docker ps -a --filter label=goblin-king.kind=project.inline.hello
```

Expected result when a matching container is still present: Goblin King-created task
containers carry labels such as goblin kind, job ID, and run ID. Short-lived successful
task containers may already be removed by the time you inspect Docker history.

## Step 9: Shut Down

Stop the adopter stack:

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

Use `--volumes --remove-orphans` only when you intentionally want to remove all local
runtime data.

## Troubleshooting

| Symptom | What To Check |
| --- | --- |
| Project goblin is missing from admin | Run `make project-discovery-reload`, then refresh **Goblin Lab**. |
| Validation status is `unknown` | Run `goblin-king workers validate --project <path> --kind <kind> --input <file> --build --require-success`. |
| Validation status is `stale` | The configured image changed; revalidate the current image digest. |
| Job is rejected before execution | Read the scheduler/run error. Missing or failed validation proof blocks container execution by default. |
| Artifact metadata is missing | Confirm the worker writes artifacts under `GOBLIN_ARTIFACT_ROOT` and includes artifact metadata in the result envelope. |
| Docker socket concern | Only the trusted Goblin King control plane should have Docker access in local Docker mode. Goblin task containers should not. |

## Next Links

- [Adopter Admin Dev/Test Stack](adopter-admin-dev-stack.md)
- [Using Goblin King As Your Project Scheduler](using-goblin-king-as-a-project-scheduler.md)
- [Using Goblin King From A Vendored Checkout](using-goblin-king-from-a-vendored-checkout.md)
- [Project Goblin Config](project-goblin-config.md)
- [Goblin Contract Validation](goblin-contract-validation.md)
- [Admin Guide](ADMIN_GUIDE.md)
