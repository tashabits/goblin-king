FROM rust:1.82-slim AS build

WORKDIR /src
COPY Cargo.toml /src/Cargo.toml
COPY src /src/src
RUN cargo build --release

FROM debian:bookworm-slim

COPY --from=build /src/target/release/hello-rust /worker/hello-rust
ENTRYPOINT ["/worker/hello-rust"]
