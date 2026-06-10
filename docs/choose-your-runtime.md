# Choose Your Goblin Runtime

Goblin King schedules containers. Pick the language inside the container by operational
fit, not by what Goblin King is written in.

| Runtime | Good Fit | Watch For |
| --- | --- | --- |
| Shell | Tiny glue tasks, filesystem checks, simple command orchestration | JSON escaping and dependency drift |
| Python | Data cleanup, API glue, quick internal tools | Keep project dependencies pinned inside the worker image |
| Node.js | JSON-heavy integrations and web-adjacent tooling | Long dependency trees and cold-start size |
| Go | Small static binaries and network tools | Rebuild image when config structs change |
| Rust | High confidence binaries, parsing, CPU-heavy helpers | Build time and dependency compilation |
| Java/.NET | Existing enterprise libraries and service clients | Larger images and runtime tuning |
| Ruby/PHP | Existing project scripts and framework-adjacent jobs | Make CLI/runtime assumptions explicit |
| WASI in a container | Extra sandbox boundary for small modules | Goblin King still runs the container, not native `.wasm` |

## Decision Rules

- Use the runtime your team can maintain.
- Keep every worker folder self-contained with its own `Dockerfile`.
- Validate the worker contract locally before deployment.
- Use `--require-success` for hello/smoke workers.
- Omit `--require-success` for intentional failure examples.

The language may be fancy. The contract should be boring.
