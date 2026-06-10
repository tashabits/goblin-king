FROM rust:1.82-slim AS build

WORKDIR /src
RUN rustup target add wasm32-wasip1
COPY Cargo.toml /src/Cargo.toml
COPY src /src/src
RUN cargo build --release --target wasm32-wasip1

FROM debian:bookworm-slim

ARG WASMTIME_VERSION=25.0.0
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl xz-utils \
    && rm -rf /var/lib/apt/lists/* \
    && curl -fsSL "https://github.com/bytecodealliance/wasmtime/releases/download/v${WASMTIME_VERSION}/wasmtime-v${WASMTIME_VERSION}-x86_64-linux.tar.xz" \
        | tar -xJ -C /opt \
    && ln -s "/opt/wasmtime-v${WASMTIME_VERSION}-x86_64-linux/wasmtime" /usr/local/bin/wasmtime

WORKDIR /worker
COPY --from=build /src/target/wasm32-wasip1/release/wasi-rust-hello.wasm /worker/worker.wasm
COPY run-wasi.sh /worker/run-wasi.sh
RUN chmod +x /worker/run-wasi.sh

ENTRYPOINT ["/worker/run-wasi.sh"]
