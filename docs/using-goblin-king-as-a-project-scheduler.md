# Using Goblin King As Your Project Scheduler

Use Goblin King when your project has background tasks or managed HTTP services that
should run as isolated, validated container-backed workloads. Your app keeps its own
domain logic. Goblin King handles project goblin discovery, validation, scheduling or
service lifecycle, execution, run history, artifacts, events, and admin inspection.

The short version: package each task or service as a container, describe it in
`goblin-king-project.json`, validate the image, then submit/schedule or start the
goblin.

If your project includes Goblin King as a submodule, subtree, or local path dependency,
see [Using Goblin King From A Vendored Checkout](using-goblin-king-from-a-vendored-checkout.md)
for the install and local stack details.

## When this is a good fit

- Trusted self-hosted projects.
- Internal automations and maintenance jobs.
- Scheduled background tasks.
- Project-scoped HTTP services that need probe/proxy/start/stop visibility.
- Artifact-producing jobs such as reports, exports, images, or PDFs.
- Mixed-language worker tasks.
- Legacy runtime wrappers that benefit from a clean container boundary.
- Jobs that should have process/container isolation from the main app.

## When this is not a good fit

- Untrusted public container execution.
- Arbitrary third-party goblins.
- Multi-tenant production without additional hardening.
- Remote, federated, or geographic runner orchestration.
- Workloads requiring strict enterprise governance that has not been added to your
  deployment.

## Mental model

Goblin King can be the workload control plane for another project, but it does not
become that project's application framework. A project goblin is a validated
container-backed workload. Goblin King schedules or manages containers, not language
runtimes. The language inside the container is an implementation detail.

Each goblin must follow the
[Goblin Container Contract](goblin-container-contract.md). Validation proves that the
container follows the contract; it does not prove that the image is trustworthy. Run only
images you build or otherwise trust.

Project config is the primary adopter path. Python package entry points can help with
metadata and tests, but they are optional. A deployed worker does not need to import
Goblin King Python code.

## Example project layout

One useful organization is one folder per worker image:

```text
my-project/
  goblin-king-project.json
  goblin-resource-policies.json
  goblins/
    invoice-renderer/
      Dockerfile
      main.py
    image-resizer/
      Dockerfile
      index.js
    report-builder/
      Dockerfile
      main.go
  inputs/
    invoice.json
    image.json
  schemas/
    invoice-renderer.input.schema.json
```

This layout is only a convention. The important part is that each background task or
service becomes a container image and is registered in project config.

## Step 1: Add Goblin King project config

Create `goblin-king-project.json` at the project root. This example uses current
`GoblinProject` fields and current resource-policy names:

```json
{
  "apiVersion": "goblin-king/v1alpha1",
  "kind": "GoblinProject",
  "registries": [],
  "entry_points": false,
  "images": "goblin-images.json",
  "api_settings": "goblin-king-api.json",
  "defaults": {
    "resources": {
      "timeout_seconds": 120,
      "cpu": {
        "request": "100m",
        "limit": "500m"
      },
      "memory": {
        "request": "128Mi",
        "limit": "512Mi"
      },
      "filesystem": {
        "read_only_root": true,
        "artifact_max_bytes": 10485760
      },
      "logs": {
        "max_bytes": 1048576
      },
      "network": {
        "mode": "none"
      },
      "concurrency": {
        "max_project_running": 10,
        "max_running": 3
      }
    }
  },
  "goblins": {
    "myproject.invoice-renderer": {
      "displayName": "Invoice Renderer",
      "image": "my-project/invoice-renderer:local",
      "context": "goblins/invoice-renderer",
      "dockerfile": "Dockerfile",
      "description": "Render invoices as PDF artifacts.",
      "inputSchema": "schemas/invoice-renderer.input.schema.json",
      "resources": {
        "timeout_seconds": 300,
        "memory": {
          "limit": "1Gi"
        }
      },
      "artifacts": {
        "enabled": true
      },
      "labels": {
        "team": "billing"
      },
      "tags": ["pdf", "billing"],
      "env": {
        "MODE": "local"
      },
      "secretRefs": ["invoice-api-token"],
      "schedule": {
        "cron": "0 * * * *",
        "enabled": false
      }
    },
    "myproject.image-resizer": {
      "displayName": "Image Resizer",
      "image": "my-project/image-resizer:local",
      "context": "goblins/image-resizer",
      "dockerfile": "Dockerfile",
      "description": "Resize uploaded images using the project default resources.",
      "inputSchema": "schemas/image-resizer.input.schema.json",
      "tags": ["images"]
    }
  }
}
```

`myproject.image-resizer` inherits the project resource defaults. The invoice renderer
inherits the defaults too, then overrides timeout and memory because PDF rendering needs
more room.

## Step 2: Write a goblin Dockerfile

Each worker folder should build a self-contained image. For example:

```dockerfile
FROM python:3.12-slim

WORKDIR /worker
COPY main.py /worker/main.py

RUN useradd --create-home --uid 10001 goblin
USER goblin

CMD ["python", "/worker/main.py"]
```

The same pattern works for Node.js, Go, Java, .NET, Ruby, PHP, shell, Rust, or a
container-wrapped WASI module. The container contract matters more than the language.

## Step 3: Read input and write a result

A minimal Python goblin reads `GOBLIN_INPUT_PATH`, writes a result envelope to
`GOBLIN_RESULT_PATH`, and writes artifacts only under `GOBLIN_ARTIFACT_ROOT`.

```python
import json
import os
from pathlib import Path


input_path = Path(os.environ["GOBLIN_INPUT_PATH"])
result_path = Path(os.environ["GOBLIN_RESULT_PATH"])
artifact_root = Path(os.environ["GOBLIN_ARTIFACT_ROOT"])

payload = json.loads(input_path.read_text(encoding="utf-8"))
invoice_id = payload.get("invoice_id", "unknown")

artifact_root.mkdir(parents=True, exist_ok=True)
artifact_path = artifact_root / f"{invoice_id}.txt"
artifact_path.write_text(f"Invoice {invoice_id}\n", encoding="utf-8")

result = {
    "status": "success",
    "data": {
        "invoice_id": invoice_id,
        "message": "invoice rendered"
    },
    "artifacts": [
        {
            "name": artifact_path.name,
            "path": artifact_path.name,
            "media_type": "text/plain"
        }
    ],
    "metrics": {},
    "handoff": [],
    "error": None
}

result_path.write_text(json.dumps(result), encoding="utf-8")
```

On failure, still write a valid result envelope when practical:

```json
{
  "status": "failed",
  "data": {},
  "artifacts": [],
  "metrics": {},
  "handoff": [],
  "error": "explain what failed"
}
```

Use a non-zero exit code for unexpected process failure. For expected domain failures,
prefer a valid failed result envelope so the run detail is readable.

## Step 4: Validate first, then schedule

Goblin King does not schedule arbitrary unvalidated container images by default.
Validation proof is tied to goblin kind, resolved image identity, contract version,
validator version, and timestamp. If the image changes, re-run validation.

Validate project config and discovery:

```bash
goblin-king project validate --project goblin-king-project.json
goblin-king project goblins list --project goblin-king-project.json
```

Build and validate the worker image:

```bash
goblin-king workers validate \
  --project goblin-king-project.json \
  --input inputs/invoice.json \
  --kind myproject.invoice-renderer \
  --build \
  --require-success
```

Check the persisted validation proof:

```bash
goblin-king workers validation-status --kind myproject.invoice-renderer
```

Common validation problems:

| Problem | What it means | Fix |
| --- | --- | --- |
| Image has not passed validation | No proof exists for this image identity. | Run `goblin-king workers validate`. |
| Validation is stale | The image identity changed since validation. | Re-run worker validation for the new image. |
| Result file missing | The worker did not write `GOBLIN_RESULT_PATH`. | Write the required result file. |
| Result JSON invalid | The result file is not a valid result envelope. | Fix the JSON shape and required fields. |
| Artifact path violation | Artifact metadata points outside the allowed artifact root. | Write artifacts under `GOBLIN_ARTIFACT_ROOT`. |

For the full validation model, see
[Goblin Contract Validation](goblin-contract-validation.md).

## Step 5: Submit an on-demand job

Run one goblin immediately through Docker:

```bash
goblin-king jobs submit myproject.invoice-renderer \
  --project goblin-king-project.json \
  --input inputs/invoice.json \
  --runtime docker
```

The command prints the job/run result. Store the run ID from that output for inspection.

## Step 6: Add a schedule

Persist a due schedule:

```bash
goblin-king schedules add myproject.invoice-renderer \
  --project goblin-king-project.json \
  --input inputs/invoice.json \
  --cron "0 * * * *" \
  --due-now
```

Run one deterministic scheduler pass:

```bash
goblin-king scheduler run-once \
  --project goblin-king-project.json \
  --runtime docker
```

For a real local stack, run the scheduler loop as a service through Docker Compose or
Helm, then manage schedules through the CLI, API, or admin interface.

For a project-oriented Docker Compose stack that mounts project config and opens the
React admin, see [Adopter Admin Dev/Test Stack](adopter-admin-dev-stack.md).
For a shorter proof checklist centered on the admin panels, see
[Testing Your Project With The Admin Panel](testing-your-project-with-the-admin-panel.md).

## Step 7: Inspect runs, results, logs, and artifacts

Inspect a run with source job metadata:

```bash
goblin-king runs show <run-id> --with-job
```

Run detail includes status, result JSON, source goblin metadata, effective policy, and
artifact metadata when the worker produced artifacts.

The React admin also has a **Runs & Artifacts** panel for browsing run details and
artifact links. Goblin King does not currently expose a separate `goblin-king artifacts
list` CLI command, so use run detail or the admin panel for artifact inspection.

## Step 8: Add resource defaults and goblin overrides

Use project defaults for the normal shape of work in your project:

```json
{
  "defaults": {
    "resources": {
      "timeout_seconds": 120,
      "memory": {"limit": "512Mi"},
      "filesystem": {"read_only_root": true},
      "logs": {"max_bytes": 1048576},
      "concurrency": {"max_project_running": 10, "max_running": 3}
    }
  }
}
```

Use per-goblin `resources` only for exceptions. Global/operator ceilings from
`goblin-resource-policies.json` still win; a goblin cannot request above the configured
ceiling. The effective policy is persisted on the job and run and is passed to workers as
`GOBLIN_EFFECTIVE_RESOURCE_POLICY_JSON`.

Concurrency can delay work without failing it. If `concurrency.max_running` or
`concurrency.max_project_running` is full, the scheduler leaves the job queued and records
a visible deferral reason.

See [Goblin Resource Policies](goblin-resource-policies.md) for detailed policy fields,
Docker mappings, Kubernetes mappings, ceilings, and proof commands.

## Safety notes

- Run only goblin images you build or trust.
- Validation proves contract compliance, not trustworthiness.
- Use resource defaults and ceilings.
- Set finite timeouts.
- Cap logs and artifacts.
- Avoid privileged containers.
- Avoid mounting host-sensitive paths.
- Do not mount the Docker socket into goblin worker containers.
- Disable network unless the worker needs it.
- Use non-root containers where practical.
- Do not expose admin or API publicly without proper auth and TLS.

## Next links

- [Goblin Container Contract](goblin-container-contract.md)
- [Project Goblin Config](project-goblin-config.md)
- [Goblin Contract Validation](goblin-contract-validation.md)
- [Goblin Resource Policies](goblin-resource-policies.md)
- [Testing Your Project With The Admin Panel](testing-your-project-with-the-admin-panel.md)
- [Project Template Quickstart](project-template-quickstart.md)
- [Adopter Guide](adopter-guide.md)
- [Security Model](security-model.md)
