# Goblin Dockerfiles

Every goblin worker folder should be self-contained and buildable as an OCI/Docker
image. Keep Dockerfiles boring, explicit, and easy to prove locally.

## Minimal Interpreted-Language Worker

```dockerfile
FROM python:3.12-slim
WORKDIR /worker
COPY worker.py /worker/worker.py
ENTRYPOINT ["python", "/worker/worker.py"]
```

Use the same shape for Node.js, Ruby, PHP, or shell: install only what the worker needs,
copy the worker source, and set an entrypoint that follows the contract.

## Compiled Multi-Stage Worker

```dockerfile
FROM golang:1.22 AS build
WORKDIR /src
COPY . .
RUN CGO_ENABLED=0 go build -o /out/worker ./cmd/worker

FROM gcr.io/distroless/static-debian12
COPY --from=build /out/worker /worker
USER 65532:65532
ENTRYPOINT ["/worker"]
```

Multi-stage builds keep compilers out of runtime images. Distroless or slim images are
good when the worker does not need a shell for debugging.

## Non-Root Worker

```dockerfile
FROM python:3.12-slim
RUN useradd --create-home --uid 10001 goblin
WORKDIR /worker
COPY worker.py /worker/worker.py
USER 10001:10001
ENTRYPOINT ["python", "/worker/worker.py"]
```

When using non-root images, make sure Goblin King's mounted contract directory is
writable by the container user.

## Read-Only Friendly Worker

Design workers so the root filesystem can be read-only. Write only to:

- `GOBLIN_RESULT_PATH`
- `GOBLIN_ARTIFACT_ROOT`
- documented temporary paths, if a later policy explicitly mounts them

Avoid writing caches, package manager state, or logs to the image filesystem at runtime.

## Container-Wrapped WASI Worker

```dockerfile
FROM debian:bookworm-slim
ARG WASMTIME_VERSION=20.0.2
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl \
  && rm -rf /var/lib/apt/lists/*
COPY worker.wasm /worker/worker.wasm
COPY entrypoint.sh /worker/entrypoint.sh
ENTRYPOINT ["/worker/entrypoint.sh"]
```

The entrypoint runs the pinned WASI runtime against `worker.wasm` and maps the same
contract environment variables and mounted paths into the WASI process. Goblin King
still schedules the container.

## Local Debugging Pattern

Use an image with a shell only while debugging:

```bash
docker run --rm -it \
  --entrypoint sh \
  -v "$PWD/.goblin-king/dev-run:/goblin" \
  my-goblin:local
```

Remove debug-only packages and shells from production images when practical. The King
enjoys a good lantern, but not one accidentally shipped into every vault.
