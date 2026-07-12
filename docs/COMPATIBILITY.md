# Compatibility Matrix

Goblin King `0.1.0` is the first project-ready internal package baseline.

| Surface | Version | Compatibility Promise |
| --- | --- | --- |
| Python package | `0.1.0` | Host projects should import stable primitives from `goblin_king`. |
| Goblin container contract | `goblin-king/v1alpha1` | Workers receive `GOBLIN_CONTRACT_VERSION` and the mounted file/env contract in `docs/goblin-container-contract.md`. |
| Registry schema | `goblin-king/v1alpha1` | JSON registry files use a top-level `goblins` array. |
| Project settings schema | `goblin-king/v1alpha1` | `goblin-king-project.json` uses `apiVersion: goblin-king/v1alpha1` and `kind: GoblinProject`. |
| Worker image map schema | `goblin-king/v1alpha1` | `goblin-images.json` uses a top-level `workers` object keyed by goblin kind. |
| Worker result contract | `goblin-king/v1alpha1` | Container workers return a `GoblinResult` envelope through Redis and fallback result JSON. |
| Worker heartbeat contract | `goblin-king/v1alpha1` | Long-running workers may publish heartbeat envelopes to Redis heartbeat transport. |
| Durable event ordering | `goblin-king/v1alpha1` | Events expose an additive integer `sequence`; consumers use it instead of wall time for causal order. |
| API settings schema | `goblin-king/v1alpha1` | `goblin-king-api.json` may point to direct registry/image paths or project settings. |

The machine-readable version of this matrix lives at
`compatibility/goblin-king-compatibility.json`.

## Upgrade Rule

Patch releases may add optional fields, commands, docs, and endpoints without breaking
existing fixtures. Breaking changes require a new compatibility fixture under
`examples/compatibility/` and explicit migration notes.
