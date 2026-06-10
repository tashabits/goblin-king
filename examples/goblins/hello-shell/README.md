# Shell Hello Goblin

This sample proves that a goblin can be plain POSIX shell. It reads the
container contract files, validates JSON with `jq`, writes a Goblin result
envelope, and exits successfully.

```powershell
docker build -t goblin-example-hello-shell:local .
```

At runtime, mount input/context/result files and set the paths with
`GOBLIN_INPUT_PATH`, `GOBLIN_CONTEXT_PATH`, and `GOBLIN_RESULT_PATH`.
