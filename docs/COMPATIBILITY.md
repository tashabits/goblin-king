# Compatibility Matrix

Goblin King `0.1.0` is the first project-ready internal package baseline.

| Surface | Version | Compatibility Promise |
| --- | --- | --- |
| Python package | `0.1.0` | Host projects should import stable primitives from `goblin_king`. |
| Goblin contract | `1` | `GoblinDefinition`, `GoblinContext`, and `GoblinResult` root exports remain the supported plugin contract. |
| Registry schema | `1` | JSON registry files use a top-level `goblins` array. |
| Project settings schema | `1` | `goblin-king-project.json` supports `registries`, `entry_points`, `images`, and `api_settings`. |
| Worker image map schema | `1` | `goblin-images.json` uses a top-level `workers` object keyed by goblin kind. |
| Worker result contract | `1` | Container workers return a `GoblinResult` envelope through Redis and fallback result JSON. |
| Worker heartbeat contract | `1` | Long-running workers may publish heartbeat envelopes to Redis heartbeat transport. |
| API settings schema | `1` | `goblin-king-api.json` may point to direct registry/image paths or project settings. |

The machine-readable version of this matrix lives at
`compatibility/goblin-king-compatibility.json`.

## Upgrade Rule

Patch releases may add optional fields, commands, docs, and endpoints without breaking
existing fixtures. Breaking changes require a new compatibility fixture under
`examples/compatibility/` and explicit migration notes.
