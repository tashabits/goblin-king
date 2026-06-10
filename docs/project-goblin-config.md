# Project Goblin Config

Project goblin config lets an adopting project define container goblins without editing
Goblin King source code or writing Python worker imports.

Current version: `goblin-king/v1alpha1`.

Use this when a project already has its own worker images and only needs Goblin King to
discover, validate, queue, schedule, and display them.

## File Shape

The existing `goblin-king-project.json` file now also accepts a versioned project config
header and an inline `goblins` map:

```json
{
  "apiVersion": "goblin-king/v1alpha1",
  "kind": "GoblinProject",
  "registries": ["registries/maintenance.json"],
  "entry_points": false,
  "images": "goblin-images.json",
  "api_settings": "goblin-king-api.json",
  "defaults": {
    "resources": {
      "timeout_seconds": 60,
      "memory": {"limit": "256Mi"},
      "filesystem": {"read_only_root": true},
      "network": {"mode": "none"}
    }
  },
  "goblins": {
    "project.invoice-renderer": {
      "displayName": "Invoice Renderer",
      "description": "Render invoices as PDFs",
      "image": "my-project/invoice-renderer:local",
      "context": "workers/invoice-renderer",
      "dockerfile": "Dockerfile",
      "inputSchema": "schemas/invoice-renderer.input.schema.json",
      "resources": {
        "timeout_seconds": 120,
        "memory": {"limit": "512Mi"}
      },
      "artifacts": {"enabled": true},
      "labels": {"team": "finance"},
      "tags": ["pdf", "billing"],
      "env": {"MODE": "local"},
      "secretRefs": ["invoice-renderer-api-token"],
      "schedule": {"cron": "0 * * * *", "enabled": false}
    }
  }
}
```

All paths resolve relative to the project config file unless they are absolute.

## Project Resource Defaults

Use `defaults.resources` when every inline goblin in a project should inherit the same
resource posture. This is useful for small adopter projects that want one default
timeout, memory cap, read-only-root setting, network mode, log limit, or concurrency
limit without repeating the same block in every goblin definition.

Project loading deep-merges `defaults.resources` into each inline goblin's `resources`.
The per-goblin value wins for the fields it names, while unspecified nested fields keep
the project default. For example, a project default of `memory.limit: 256Mi` plus a
goblin override of `memory.limit: 512Mi` gives that goblin a `512Mi` effective limit;
a default `filesystem.read_only_root: true` remains in effect unless the goblin changes
that field.

If a `goblin-resource-policies.json` file is present next to the project config, project
validation checks both `defaults.resources` and each merged per-goblin policy against
the file's ceilings. `goblin-king project validate` prints the raw
`defaults.resources` block when present so operators can see the project-level policy
source during smoke proof.

## Fields

| Field | Meaning |
| --- | --- |
| `apiVersion` | Must be `goblin-king/v1alpha1`. Older unversioned project settings remain accepted for compatibility, but new project-owned goblin config should include this field. |
| `kind` | Must be `GoblinProject` when present. |
| `registries` | Existing registry JSON files to merge. |
| `entry_points` | Whether to discover installed `goblin_king.goblins` entry points. |
| `images` | Existing worker image map file. |
| `api_settings` | API settings file for the project. |
| `defaults` | Optional project-wide defaults for inline goblin definitions. |
| `defaults.resources` | Resource policy fields deep-merged into every inline goblin's `resources` before validation and discovery. |
| `goblins` | Inline project-owned container goblin definitions. |
| `displayName` | Optional admin/API display name. |
| `description` | Human-facing description stored in goblin metadata. |
| `image` | Worker image tag used by Docker/Kubernetes runtime. |
| `context` | Worker build context path. |
| `dockerfile` | Dockerfile name under the context. |
| `inputSchema` | Optional JSON schema path for future validation/docs. |
| `resources` | Per-goblin resource policy override. These fields are merged over `defaults.resources`; nested objects such as `memory`, `filesystem`, `network`, `logs`, and `concurrency` merge key by key. |
| `artifacts` | Artifact expectations for project docs and future validation. |
| `labels` / `tags` | Project-owned metadata for grouping and admin display. |
| `env` | Safe non-secret environment metadata. |
| `secretRefs` | Secret names only; values are rejected. |
| `schedule` | Optional schedule metadata used by later adopter workflows. |

## Discovery Behavior

Inline goblins are converted into normal `GoblinDefinition` entries with the placeholder
module `goblin_king.container_only`. Docker and Kubernetes execution do not import that
module; they use the configured worker image. If someone tries to run an inline goblin
with the in-process runtime, the placeholder fails clearly.

Inline worker image definitions are merged with the project image map. Duplicate worker
image mappings are rejected so deployment proof cannot silently point a kind at two
images.

The API and React admin mark these goblins with source `project-config`.

Project-defined goblins use the same mandatory validation gate as built-in examples:
the scheduler does not execute a container image by default unless its resolved image
identity has passing proof for the declared contract and validator version. See
[Goblin Contract Validation](goblin-contract-validation.md) for the canonical proof
lifecycle and failure mapping.

Project configs may set shared runtime policy under `defaults.resources`. Each inline
goblin may then declare a smaller `resources` block for exceptions. Goblin King resolves
the runtime policy as operator defaults, then project defaults, then the goblin override;
the final effective policy is persisted on jobs/runs and visible in API, CLI, and admin
run detail. Unknown resource fields fail validation so typos do not silently fall through.

## Validate

```bash
goblin-king project validate --project examples/adopting-project/goblin-king-project.json
goblin-king project goblins list --project examples/adopting-project/goblin-king-project.json
```

Expected proof includes `project.inline.hello`, `project.maintenance.hello`, and
`project.reports.long-service`.
