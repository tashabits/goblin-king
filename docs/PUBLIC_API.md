# Goblin King Public API Boundary

This boundary is for projects that install Goblin King as an internal dependency. The
goal is boring adoption: project code should import stable contracts and helpers from
`goblin_king`, while the King keeps runtime, persistence, and admin details behind the
CLI/API where possible.

## Stable Root Imports

Use these imports from `goblin_king` in host projects and generated goblin packages:

- Version constants: `API_SETTINGS_SCHEMA_VERSION`,
  `GOBLIN_CONTAINER_CONTRACT_VERSION`,
  `PROJECT_CONFIG_API_VERSION`, `PROJECT_CONFIG_KIND`,
  `REGISTRY_SCHEMA_VERSION`, `WORKER_IMAGE_MAP_SCHEMA_VERSION`,
  `WORKER_RESULT_CONTRACT_VERSION`, and `WORKER_HEARTBEAT_CONTRACT_VERSION`.
- Contracts: `GoblinDefinition`, `GoblinContext`, `GoblinResult`, job/run/schedule,
  fanout, event, heartbeat, artifact, handoff, auth, project, and worker validation
  record models.
- Registry helpers: `GoblinRegistry`, `RegistryError`, `ENTRY_POINT_GROUP`, and
  `discover_entry_point_definitions`.
- Project settings: `ProjectSettings` and `ProjectSettingsError`.
- Worker image settings: `WorkerImageMap`, `WorkerImageDefinition`, and
  `WorkerConfigError`.
- API settings: `ApiSettings` and `ApiSettingsError`.
- Notebook helpers: `GoblinKingNotebookClient`, `NotebookFunctionGoblin`, and
  `NotebookASGIService`.
- Template helpers: `init_package` and `TemplateError`.

Generated goblin packages should normally need only:

```python
from goblin_king import GoblinContext, GoblinDefinition, GoblinResult
```

These imports are stable helper APIs for Python package definitions and tests. They do
not replace the [Goblin Container Contract](goblin-container-contract.md): deployed
goblins are still OCI/Docker containers.

Host-project integration scripts may also use:

```python
from goblin_king import (
    GOBLIN_CONTAINER_CONTRACT_VERSION,
    ProjectSettings,
    GoblinRegistry,
    WorkerImageMap,
)
```

## Explicit Heavy Imports

Runtime and control-plane objects stay supported, but importing them should be explicit
so simple adopter imports do not load FastAPI, SQLite storage, Docker/Kubernetes runtime
helpers, scheduler code, or CLI dependencies:

```python
from goblin_king.api import create_app
from goblin_king.scheduler import Scheduler
from goblin_king.store import SQLiteStore
```

For compatibility, the package root still exposes lazy shims for `create_app`,
`Scheduler`, and `SQLiteStore`. Those names load their heavy modules only when the
attribute is requested. New code should prefer the explicit imports above.

## Semi-Public Command Surface

The CLI is a supported integration surface for host projects:

- `goblin-king project validate`
- `goblin-king project goblins list`
- `goblin-king project init-package`
- `goblin-king workers build`
- `goblin-king workers validate` (`--runtime docker` by default; `kubernetes` is
  explicit)
- `goblin-king api run`
- `goblin-king scheduler run`

Prefer these commands over importing runtime internals when the project only needs to
validate, deploy, run, or prove goblins.

## Generic Kubernetes Worker Validation API

`POST /admin/workers/validate-kubernetes` is the supported in-cluster proof operation
for generic workers already present in the active registry and worker image map. It
requires an admin bearer token. The request is additive to existing APIs:

```json
{
  "kinds": ["example.hello"],
  "input": {"message": "proof"},
  "require_success": true,
  "timeout_seconds": 120
}
```

Omit `kinds` to validate all active registry definitions. `timeout_seconds` must be
between 1 and 3600. The caller cannot supply an image, command, mount, secret, service
account, or raw Job manifest.

The endpoint reads the typed `kubernetes_runtime` member from API settings and uses the
same runtime factory as scheduler and notebook execution. Forwarder identity, worker and
forwarder pull policies, workload pull-secret names, namespace discovery, and bounded
diagnostics therefore cannot be selected independently by this request. The configured
workload-security profile and per-kind ServiceAccount decision are included in restricted
validation identity, so a legacy proof does not authorize a `restricted-v1` execution.

The response contains `validations`, one `WorkerValidationResult` per selected kind.
Existing result fields remain unchanged. The additive `artifacts` field returns
validated result-envelope metadata, and `logs` returns bounded Kubernetes pod logs by
container name. Each result is persisted as a `WorkerValidationRecord`; a passing record
uses the exact identity the Kubernetes scheduler checks.

The operation is synchronous and may remain open until the requested Job deadline plus
runtime completion overhead. Repeating it creates a new immutable proof record; it does
not mutate prior records. See
[Generic Kubernetes Worker Validation Proof](kubernetes-generic-worker-validation-proof.md).

## Internal Modules

Modules such as `goblin_king.runtime`, `goblin_king.store`, `goblin_king.auth`,
`goblin_king.events`, `goblin_king.fanout`, `goblin_king.api_models`,
`goblin_king.deployment`, and cleanup helper modules such as
`goblin_king.api_artifacts`, `goblin_king.api_runtime`, `goblin_king.api_state`,
`goblin_king.cli_support`, `goblin_king.runtime_helpers`, and `goblin_king.store_rows`
remain importable for Goblin King itself, but their non-root symbols are not promised
stable for adopting projects. If a project needs one of those internals, first promote
the needed behavior to the root boundary or the CLI/API in a focused compatibility PR.

## Internal Wheel Versioning

Goblin King uses internal package compatibility before public PyPI compatibility.

- Patch versions may add fields, commands, docs, and optional behavior without breaking
  existing goblin packages.
- Minor versions may add public root exports or API endpoints.
- Breaking changes to goblin contracts, registry schema, worker image maps, API settings,
  or worker result/heartbeat envelopes require an explicit compatibility note and a
  migration guide.
- Host projects should pin the internal wheel and Docker image tag together for each
  deployment.

The compatibility matrix should track:

- Goblin contract version.
- Registry schema version.
- Worker image map schema version.
- Worker result/heartbeat contract version.
- API settings schema version.
