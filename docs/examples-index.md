# Goblin Examples Index

This index gathers the sample goblins by purpose. Every worker is a
contract-compliant container, even when the registry uses Python metadata helpers.

The root `demo-goblins.json` and `demo-images.json` files combine the core examples,
cross-language hello workers, WASI wrappers, and behavior samples. The React admin uses
that combined demo set by default so each language goblin can be selected explicitly in
Goblin Lab.

## Hello Containers

| Kind | Folder | Runtime | Proof |
| --- | --- | --- | --- |
| `example.hello-dotnet` | `examples/goblins/hello-dotnet` | .NET 8 | Cross-language runtime proof |
| `example.hello-go` | `examples/goblins/hello-go` | Go | Cross-language runtime proof |
| `example.hello-java` | `examples/goblins/hello-java` | Java 21 | Cross-language runtime proof |
| `example.hello-node` | `examples/goblins/hello-node` | Node.js | Cross-language runtime proof |
| `example.hello-php` | `examples/goblins/hello-php` | PHP CLI | Cross-language runtime proof |
| `example.hello-python` | `examples/goblins/hello-python` | Python stdlib | Cross-language runtime proof |
| `example.hello-ruby` | `examples/goblins/hello-ruby` | Ruby | Cross-language runtime proof |
| `example.hello-rust` | `examples/goblins/hello-rust` | Rust | Cross-language runtime proof |
| `example.hello-shell` | `examples/goblins/hello-shell` | Shell + jq | Cross-language runtime proof |
| `example.wasi-c-hello` | `examples/goblins/wasi-c-hello` | C/WASI on Wasmtime | Cross-language runtime proof |
| `example.wasi-rust-hello` | `examples/goblins/wasi-rust-hello` | Rust/WASI on Wasmtime | Cross-language runtime proof |

Use:

```bash
make validate-cross-language-workers
```

## Behavior Containers

| Kind | Folder | Demonstrates |
| --- | --- | --- |
| `example.behavior-node-artifact` | `examples/goblins/behavior-node-artifact` | Artifact file and metadata |
| `example.behavior-python-progress` | `examples/goblins/behavior-python-progress` | Progress/logging and metrics |
| `example.behavior-python-slow-cancellable` | `examples/goblins/behavior-python-slow-cancellable` | Timeout-ish loops and SIGTERM handling |
| `example.behavior-go-transform` | `examples/goblins/behavior-go-transform` | Input transforms and context reading |
| `example.behavior-shell-failure` | `examples/goblins/behavior-shell-failure` | Honest failed result envelopes |
| `example.behavior-wasi-c-context` | `examples/goblins/behavior-wasi-c-context` | WASI context reads through Wasmtime |

Use:

```bash
make validate-behavior-workers
```

## Worker Backbone Recipes

| Kind | Folder | Demonstrates |
| --- | --- | --- |
| `example.worker-backbone.normalize-note` | `examples/worker-backbone/workers/normalize-note` | Generic task worker with a deterministic result envelope |
| `example.worker-backbone.artifact-manifest` | `examples/worker-backbone/workers/artifact-manifest` | Artifact-producing task worker and metadata |
| `example.worker-backbone.local-rag` | `examples/worker-backbone/rag-first-use-case` | Local RAG-style retrieval over checked-in fixtures |
| `example.worker-backbone.catalog-service` | `examples/worker-backbone/workers/catalog-service` | Project-configured service worker with `/healthz` proof |

Use:

```bash
goblin-king project validate --project examples/worker-backbone/goblin-king-project.json
python -m pytest tests/test_worker_backbone_examples.py
make worker-backbone-proof
make rag-profile-proof
```

The worker backbone fixture is local-only: no external model, cloud service, or
credential is required for the RAG first-use-case proof.

The King likes demos best when they can be rebuilt while someone skeptical is watching.
