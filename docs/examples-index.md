# Goblin Examples Index

This index gathers the sample goblins by purpose. Every worker is a
contract-compliant container, even when the registry uses Python metadata helpers.

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

The King likes demos best when they can be rebuilt while someone skeptical is watching.
