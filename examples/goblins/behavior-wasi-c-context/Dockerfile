FROM debian:bookworm-slim AS build

RUN apt-get update \
    && apt-get install -y --no-install-recommends clang lld libclang-rt-14-dev-wasm32 wasi-libc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
COPY worker.c /src/worker.c
RUN mkdir -p /out \
    && clang --target=wasm32-wasi --sysroot=/usr -O2 -Wl,--export=main -o /out/worker.wasm /src/worker.c

FROM debian:bookworm-slim

ARG WASMTIME_VERSION=25.0.0
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl xz-utils \
    && rm -rf /var/lib/apt/lists/* \
    && curl -fsSL "https://github.com/bytecodealliance/wasmtime/releases/download/v${WASMTIME_VERSION}/wasmtime-v${WASMTIME_VERSION}-x86_64-linux.tar.xz" \
        | tar -xJ -C /opt \
    && ln -s "/opt/wasmtime-v${WASMTIME_VERSION}-x86_64-linux/wasmtime" /usr/local/bin/wasmtime

WORKDIR /worker
COPY --from=build /out/worker.wasm /worker/worker.wasm
COPY run-wasi.sh /worker/run-wasi.sh
RUN chmod +x /worker/run-wasi.sh

ENTRYPOINT ["/worker/run-wasi.sh"]
