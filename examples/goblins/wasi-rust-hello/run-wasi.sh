#!/bin/sh
set -eu

wasmtime run \
  --dir /goblin \
  --env "GOBLIN_INPUT_PATH=${GOBLIN_INPUT_PATH:?GOBLIN_INPUT_PATH is required}" \
  --env "GOBLIN_CONTEXT_PATH=${GOBLIN_CONTEXT_PATH:?GOBLIN_CONTEXT_PATH is required}" \
  --env "GOBLIN_RESULT_PATH=${GOBLIN_RESULT_PATH:?GOBLIN_RESULT_PATH is required}" \
  --env "GOBLIN_ARTIFACT_ROOT=${GOBLIN_ARTIFACT_ROOT:-/goblin/artifacts}" \
  --env "GOBLIN_RUN_ID=${GOBLIN_RUN_ID:-unknown-run}" \
  --env "GOBLIN_JOB_ID=${GOBLIN_JOB_ID:-unknown-job}" \
  --env "GOBLIN_KIND=${GOBLIN_KIND:-example.wasi-rust-hello}" \
  /worker/worker.wasm
