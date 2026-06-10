# WASI Rust Hello Goblin

This sample compiles Rust to `wasm32-wasip1`, then wraps the `.wasm` module in a
normal OCI container with a pinned Wasmtime runtime. Goblin King still launches a
container; the container runs WASI internally.

```powershell
docker build -t goblin-example-wasi-rust-hello:local .
```

The wrapper passes the same mounted contract paths into the WASI module. The
module reads input/context JSON and writes the Goblin result envelope.
