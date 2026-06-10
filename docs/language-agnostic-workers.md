# Language-Agnostic Workers

Goblin King does not care what language lives inside a goblin container. It cares that
the container follows the [Goblin Container Contract](goblin-container-contract.md).

## Supported Shape

Any runtime is acceptable when it can:

- Read environment variables.
- Read mounted JSON files.
- Write a JSON result file.
- Write artifacts under the mounted artifact root.
- Print logs to stdout/stderr.
- Exit with a meaningful status code.

That includes Go, Rust, Node.js, Java, .NET/C#, Ruby, PHP, shell, Python,
container-wrapped WASI/WebAssembly, and other container-packaged runtimes.

## Optional Python Helpers

Python helpers are useful for:

- Registry definition packages.
- Local unit tests.
- Generated starter packages.
- Trusted in-process debugging.

They are not required for worker containers. A Go worker that writes the same result
JSON is just as much a goblin as a Python worker.

## WASI/WebAssembly

WASI/WebAssembly workers must be container-wrapped:

```text
Goblin King -> OCI container -> WASI runtime -> .wasm module
```

The container entrypoint is responsible for running the WASI runtime and exposing the
same mounted paths and environment variables to the module. Goblin King does not launch
`.wasm` files directly.

## Choosing A Runtime

Use the language that makes the job easy to maintain:

- Go or Rust for small static binaries.
- Node.js for JSON-heavy integrations.
- Java or .NET for existing enterprise libraries.
- Ruby or PHP for existing project scripts.
- Shell for tiny glue jobs with few dependencies.
- Python when project teams already maintain Python helpers.
- WASI/WebAssembly when a second sandbox inside the container is useful and the
  toolchain is reliable enough for the project.

The runtime may be whimsical. The result envelope may not.
