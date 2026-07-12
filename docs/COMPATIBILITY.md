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
| API settings schema | `goblin-king/v1alpha1` | `goblin-king-api.json` may point to direct registry/image paths or project settings and may add typed `kubernetes_runtime` image settings. |
| Kubernetes runtime constructor | `goblin-king/v1alpha1` | The established `goblin_king.runtime.KubernetesRuntime` import and legacy image/pull-policy arguments remain valid; new settings are additive. |
| Generic Kubernetes validation | `goblin-king/v1alpha1` | The admin endpoint and explicit CLI runtime are additive; Docker remains the CLI default, and Kubernetes proof shares the typed runtime factory/settings and restricted workload identity used by scheduling. |
| Helm control image values | `goblin-king/v1alpha1` | Repository/tag and map/string pull-secret forms remain supported; optional digest and forwarder values are additive, with digest precedence. |
| Kubernetes workload security | `legacy` / `restricted-v1` | `legacy` remains the default and preserves generated Pod shape. `restricted-v1` is opt-in and binds validation proof to its effective controls. |
| Kubernetes worker ServiceAccount | `restricted-v1` | Configuration accepts only explicit kind-to-name bindings. Automatic token mounting stays disabled; a bounded projected token is mounted only in that worker. |
| Kubernetes artifact retention | `goblin-king/v1alpha1` | Helm-backed task artifacts are retained before Job cleanup; worker result and artifact JSON shapes remain unchanged and actual size/digest metrics are additive. |

The machine-readable version of this matrix lives at
`compatibility/goblin-king-compatibility.json`.

## Upgrade Rule

Patch releases may add optional fields, commands, docs, and endpoints without breaking
existing fixtures. Breaking changes require a new compatibility fixture under
`examples/compatibility/` and explicit migration notes.

The event-ordering update does not add a Job or Run status, replace a result envelope, or require
new constructor arguments. Pre-sequence EventRecord payloads remain valid and receive sequence zero
until persisted. `SQLiteStore.save_event()` retains its original no-value return; the ordered
delivery path uses a new internal persistence method. The nullable Run finish field remains part of
the Python model for existing callers, while SQLite normalizes terminal rows before API reads.

Kubernetes artifact retention adds optional scheduler environment and an optional, defaulted
`KubernetesRuntime` constructor value. Existing constructors, CLI defaults, Docker artifact paths,
API routes, database rows, and result/artifact object shapes remain valid. A Kubernetes result that
declares artifacts now fails honestly when durable retention is unavailable instead of persisting
metadata for transient bytes.
