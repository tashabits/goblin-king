FROM rust:1.82-slim AS build

WORKDIR /src
RUN rustup target add wasm32-wasip1
COPY Cargo.toml /src/Cargo.toml
COPY src /src/src
RUN cargo build --release --target wasm32-wasip1

FROM python:3.12-slim-bookworm

ARG WASMTIME_VERSION=25.0.0
RUN pip install --no-cache-dir "wasmtime==${WASMTIME_VERSION}"

WORKDIR /worker
COPY --from=build /src/target/wasm32-wasip1/release/wasi-rust-hello.wasm /worker/worker.wasm
COPY run-wasi.sh run-wasi.py /worker/
RUN chmod +x /worker/run-wasi.sh

ENTRYPOINT ["/worker/run-wasi.sh"]
