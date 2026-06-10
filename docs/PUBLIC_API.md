# Goblin King Public API Boundary

This boundary is for projects that install Goblin King as an internal dependency. The
goal is boring adoption: project code should import stable contracts and helpers from
`goblin_king`, while the King keeps runtime, persistence, and admin details behind the
CLI/API where possible.

## Stable Root Imports

Use these imports from `goblin_king` in host projects and generated goblin packages:

- Contracts: `GoblinDefinition`, `GoblinContext`, `GoblinResult`, job/run/schedule,
  fanout, event, heartbeat, artifact, handoff, auth, and project record models.
- Registry helpers: `GoblinRegistry`, `RegistryError`, `ENTRY_POINT_GROUP`, and
  `discover_entry_point_definitions`.
- Project settings: `ProjectSettings` and `ProjectSettingsError`.
- Worker image settings: `WorkerImageMap`, `WorkerImageDefinition`, and
  `WorkerConfigError`.
- API and scheduler entrypoints: `ApiSettings`, `ApiSettingsError`, `create_app`,
  `Scheduler`, and `SQLiteStore`.
- Template helpers: `init_package` and `TemplateError`.

Generated goblin packages should normally need only:

```python
from goblin_king import GoblinContext, GoblinDefinition, GoblinResult
```

Host-project integration scripts may also use:

```python
from goblin_king import ProjectSettings, GoblinRegistry, WorkerImageMap
```

## Semi-Public Command Surface

The CLI is a supported integration surface for host projects:

- `goblin-king project validate`
- `goblin-king project goblins list`
- `goblin-king project init-package`
- `goblin-king workers build`
- `goblin-king api run`
- `goblin-king scheduler run`

Prefer these commands over importing runtime internals when the project only needs to
validate, deploy, run, or prove goblins.

## Internal Modules

Modules such as `goblin_king.runtime`, `goblin_king.store`, `goblin_king.auth`,
`goblin_king.events`, `goblin_king.fanout`, `goblin_king.api_models`, and
`goblin_king.deployment` remain importable for Goblin King itself, but their non-root
symbols are not promised stable for adopting projects. If a project needs one of those
internals, first promote the needed behavior to the root boundary or the CLI/API in a
focused compatibility PR.

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
