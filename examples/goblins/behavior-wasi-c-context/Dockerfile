FROM debian:bookworm-slim AS build

RUN apt-get update \
    && apt-get install -y --no-install-recommends clang lld libclang-rt-14-dev-wasm32 wasi-libc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
COPY worker.c /src/worker.c
RUN mkdir -p /out \
    && clang --target=wasm32-wasi --sysroot=/usr -O2 -Wl,--export=main -o /out/worker.wasm /src/worker.c

FROM python:3.12-slim-bookworm

ARG WASMTIME_VERSION=25.0.0
RUN pip install --no-cache-dir "wasmtime==${WASMTIME_VERSION}"

WORKDIR /worker
COPY --from=build /out/worker.wasm /worker/worker.wasm
COPY run-wasi.sh run-wasi.py /worker/
RUN chmod +x /worker/run-wasi.sh

ENTRYPOINT ["/worker/run-wasi.sh"]
