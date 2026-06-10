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

## Fields

| Field | Meaning |
| --- | --- |
| `apiVersion` | Must be `goblin-king/v1alpha1`. Older unversioned project settings remain accepted for compatibility, but new project-owned goblin config should include this field. |
| `kind` | Must be `GoblinProject` when present. |
| `registries` | Existing registry JSON files to merge. |
| `entry_points` | Whether to discover installed `goblin_king.goblins` entry points. |
| `images` | Existing worker image map file. |
| `api_settings` | API settings file for the project. |
| `goblins` | Inline project-owned container goblin definitions. |
| `displayName` | Optional admin/API display name. |
| `description` | Human-facing description stored in goblin metadata. |
| `image` | Worker image tag used by Docker/Kubernetes runtime. |
| `context` | Worker build context path. |
| `dockerfile` | Dockerfile name under the context. |
| `inputSchema` | Optional JSON schema path for future validation/docs. |
| `resources` | Inline resource policy metadata for project docs and future enforcement. |
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

## Validate

```bash
goblin-king project validate --project examples/adopting-project/goblin-king-project.json
goblin-king project goblins list --project examples/adopting-project/goblin-king-project.json
```

Expected proof includes `project.inline.hello`, `project.maintenance.hello`, and
`project.reports.long-service`.
