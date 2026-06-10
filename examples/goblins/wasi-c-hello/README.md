# WASI C Hello Goblin

This sample compiles a small C program to `wasm32-wasi` with Debian's
`clang`/`wasi-libc` packages, then wraps the module in a normal container with
Wasmtime.

```powershell
docker build -t goblin-example-wasi-c-hello:local .
```

It exists as the boring second WASI proof: not native WASI scheduling, just a
normal container that runs a `.wasm` module inside.
