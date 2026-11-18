# WASI C Context Behavior Goblin

Runs a C `wasm32-wasi` module through Wasmtime, reads input/context files, and
returns context-aware result metadata.

```powershell
docker build -t goblin-example-behavior-wasi-c-context:local .
```

I install the pinned Wasmtime Python package from PyPI and use its WASI bindings
to run the compiled module. The wrapper exposes only the declared contract
environment and mount paths. See the [Wasmtime API documentation](https://bytecodealliance.github.io/wasmtime-py/).
